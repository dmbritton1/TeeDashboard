"""FastAPI server for the t-shirt design pipeline dashboard."""
import csv
import datetime
import io
import os
import tempfile
import zipfile

import requests
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.background import BackgroundTask

import db
import pipeline
import printify
import upscale
import worker

load_dotenv()
BASE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROMPT = (
    "Give me 20 t-shirt design ideas for [niche]. "
    "Format each as one line: phrase | style keywords. "
    "Example: reel cool dad | vintage, distressed, lake colors"
)
os.makedirs(os.path.join(BASE, "designs"), exist_ok=True)
db.init()
with db.connect() as con:
    # requeue rows orphaned by a shutdown mid-generation
    con.execute("UPDATE designs SET status = 'queued', progress = 0 WHERE status = 'generating'")
worker.start()

app = FastAPI(title="T-Shirt Design Pipeline")
app.mount("/designs", StaticFiles(directory=os.path.join(BASE, "designs")), name="designs")
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


def require_access_code(x_access_code: str | None = Header(default=None)) -> None:
    """Gate generation once a shared code is set; open when no code exists."""
    code = db.get_setting("access_code")
    if code and x_access_code != code:
        raise HTTPException(401, "Access code required")


def _queue_full() -> bool:
    with db.connect() as con:
        n = con.execute(
            "SELECT COUNT(*) AS c FROM designs WHERE status IN ('queued', 'generating')"
        ).fetchone()["c"]
    return n >= worker.MAX_QUEUE


class GenerateBody(BaseModel):
    text: str
    variations: int = 2
    style: str = ""


class PatchBody(BaseModel):
    tags: str | None = None
    rating: int | None = None


class TestBody(BaseModel):
    text: str


class SettingsBody(BaseModel):
    gemini_api_key: str = ""
    printify_api_token: str = ""
    printify_shop_id: str = ""
    access_code: str = ""
    prompt_template: str = ""


@app.post("/api/generate")
def generate(body: GenerateBody, _gate: None = Depends(require_access_code)):
    items = pipeline.parse_input(body.text)
    if not items:
        raise HTTPException(400, "No valid lines found")
    if _queue_full():
        raise HTTPException(429, "Queue is full - try again shortly")
    with db.connect() as con:
        for phrase, filters in items:
            filters = pipeline.style_filters(body.style, filters)
            for _ in range(body.variations):
                con.execute(
                    "INSERT INTO designs (phrase, filters, status) VALUES (?, ?, 'queued')",
                    (phrase, filters),
                )
    return {"queued": len(items) * body.variations}


@app.post("/api/test")
def generate_test(body: TestBody, _gate: None = Depends(require_access_code)):
    """Queue one scratch image from the raw prompt - bypasses the t-shirt template and pipeline."""
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Enter a prompt")
    if _queue_full():
        raise HTTPException(429, "Queue is full - try again shortly")
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO designs (phrase, filters, status, test) VALUES (?, '', 'queued', 1)",
            (text,),
        )
    return {"id": cur.lastrowid}


@app.get("/api/styles")
def list_styles():
    return {group: list(labels) for group, labels in pipeline.STYLE_GROUPS.items()}


@app.get("/api/designs")
def list_designs():
    with db.connect() as con:
        rows = con.execute("SELECT * FROM designs ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


@app.patch("/api/designs/{design_id}")
def patch_design(design_id: int, body: PatchBody):
    sets, vals = [], []
    if body.tags is not None:
        sets.append("tags = ?")
        vals.append(body.tags.strip())
    if body.rating is not None:
        sets.append("rating = ?")
        vals.append(max(0, min(5, body.rating)))
    if not sets:
        raise HTTPException(400, "Nothing to update")
    with db.connect() as con:
        cur = con.execute(
            "UPDATE designs SET %s WHERE id = ?" % ", ".join(sets), (*vals, design_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Design not found")
    return {"ok": True}


@app.delete("/api/designs/{design_id}")
def delete_design(design_id: int, _gate: None = Depends(require_access_code)):
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (design_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Design not found")
        # scratch images never enter the pipeline, so the review-state guard
        # that protects approved and published work doesn't apply to them
        if not row["test"] and row["status"] not in ("queued", "rejected", "failed"):
            raise HTTPException(409, "Only queued, rejected, or failed designs can be deleted")
        con.execute("DELETE FROM designs WHERE id = ?", (design_id,))
    for f in (row["file"], row["print_file"]):
        if f:
            try:
                os.remove(os.path.join(BASE, f))
            except FileNotFoundError:
                pass
    return {"ok": True}


def _set_status(design_id: int, to: str, allowed: tuple[str, ...]) -> None:
    with db.connect() as con:
        cur = con.execute(
            "UPDATE designs SET status = ? WHERE id = ? AND status IN (%s)"
            % ",".join("?" * len(allowed)),
            (to, design_id, *allowed),
        )
        if cur.rowcount == 0:
            raise HTTPException(409, "Design is not in a valid state for that action")


@app.post("/api/designs/{design_id}/approve")
def approve(design_id: int, _gate: None = Depends(require_access_code)):
    _set_status(design_id, "approved", ("pending",))
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = datetime('now') WHERE id = ?", (design_id,))
        row = con.execute("SELECT file FROM designs WHERE id = ?", (design_id,)).fetchone()
    if row and row["file"]:
        upscale.upscale(design_id, os.path.join(BASE, row["file"]))
    return {"ok": True}


@app.post("/api/designs/{design_id}/reject")
def reject(design_id: int, _gate: None = Depends(require_access_code)):
    _set_status(design_id, "rejected", ("pending",))
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = datetime('now') WHERE id = ?", (design_id,))
    return {"ok": True}


@app.post("/api/designs/{design_id}/retry")
def retry(design_id: int, _gate: None = Depends(require_access_code)):
    if _queue_full():
        raise HTTPException(429, "Queue is full - try again shortly")
    _set_status(design_id, "queued", ("failed", "rejected"))
    return {"ok": True}


@app.post("/api/designs/{design_id}/regenerate")
def regenerate(design_id: int, _gate: None = Depends(require_access_code)):
    with db.connect() as con:
        row = con.execute(
            "SELECT phrase, filters FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Design not found")
        if _queue_full():
            raise HTTPException(429, "Queue is full - try again shortly")
        con.execute(
            "INSERT INTO designs (phrase, filters, status) VALUES (?, ?, 'queued')",
            (row["phrase"], row["filters"]),
        )
    return {"ok": True}


@app.post("/api/designs/{design_id}/unreview")
def unreview(design_id: int, _gate: None = Depends(require_access_code)):
    _set_status(design_id, "pending", ("approved", "rejected"))
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = NULL WHERE id = ?", (design_id,))
    return {"ok": True}


@app.post("/api/designs/{design_id}/publish")
def publish(design_id: int, _gate: None = Depends(require_access_code)):
    if not (db.get_setting("printify_api_token") and db.get_setting("printify_shop_id")):
        raise HTTPException(400, "Printify not configured - add your token and shop ID in settings")
    with db.connect() as con:
        row = con.execute(
            "SELECT * FROM designs WHERE id = ? AND status = 'approved'", (design_id,)
        ).fetchone()
    if not row:
        raise HTTPException(409, "Design must be approved first")
    if not row["print_file"] and not row["error"]:
        raise HTTPException(409, "Design is still upscaling - try again shortly")
    row = dict(row)
    if row["file"]:
        row["file"] = os.path.join(BASE, row["file"])
    if row["print_file"]:
        row["print_file"] = os.path.join(BASE, row["print_file"])
    try:
        product_id = printify.publish(row)
    except Exception as e:
        msg = ("publish failed: %s" % e)[:500]
        with db.connect() as con:
            con.execute("UPDATE designs SET error = ? WHERE id = ?", (msg, design_id))
        raise HTTPException(502, "Printify error: %s" % e)
    with db.connect() as con:
        con.execute(
            "UPDATE designs SET status = 'published', error = NULL, product_id = ? WHERE id = ?",
            (str(product_id), design_id),
        )
    return {"product_id": product_id}


@app.get("/api/settings")
def get_settings():
    keys = ("gemini_api_key", "printify_api_token", "printify_shop_id")
    out = {k: bool(db.get_setting(k)) for k in keys}
    out["prompt_template"] = db.get_setting("prompt_template") or DEFAULT_PROMPT
    return out


@app.post("/api/settings")
def save_settings(body: SettingsBody, _gate: None = Depends(require_access_code)):
    for k, v in body.model_dump().items():
        if v.strip():
            db.set_setting(k, v.strip())
    return {"ok": True}


@app.post("/api/test/gemini")
def test_gemini():
    key = db.get_setting("gemini_api_key")
    if not key:
        return {"ok": False, "message": "No Gemini key saved yet"}
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key}, timeout=15,
        )
    except Exception as e:
        return {"ok": False, "message": "Couldn't reach Google: %s" % e}
    if r.status_code == 200:
        return {"ok": True, "message": "Gemini key works"}
    return {"ok": False, "message": "Google says: %s" % r.text[:300]}


@app.post("/api/test/printify")
def test_printify():
    token = db.get_setting("printify_api_token")
    shop = db.get_setting("printify_shop_id")
    if not (token and shop):
        return {"ok": False, "message": "Save a Printify token and shop ID first"}
    try:
        r = requests.get(
            "https://api.printify.com/v1/shops.json",
            headers={"Authorization": "Bearer %s" % token}, timeout=15,
        )
    except Exception as e:
        return {"ok": False, "message": "Couldn't reach Printify: %s" % e}
    if r.status_code != 200:
        return {"ok": False, "message": "Printify says: %s" % r.text[:300]}
    shops = r.json()
    if any(str(s.get("id")) == str(shop) for s in shops):
        return {"ok": True, "message": "Printify connected"}
    names = ", ".join("%s (%s)" % (s.get("title"), s.get("id")) for s in shops) or "none"
    return {"ok": False, "message": "Token works, but shop %s isn't on this account. Your shops: %s" % (shop, names)}


@app.get("/api/export.csv")
def export_csv():
    with db.connect() as con:
        rows = con.execute(
            "SELECT id, phrase, filters, status, tags, rating, product_id, created_at "
            "FROM designs ORDER BY id"
        ).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "phrase", "style", "status", "tags", "rating", "product_id", "created_at"])
    for r in rows:
        w.writerow(list(r))
    return Response(
        content=buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="compound-designs.csv"'},
    )


@app.get("/api/backup")
def backup():
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(db.DB_PATH, "designs.db")
        ddir = os.path.join(BASE, "designs")
        for name in sorted(os.listdir(ddir)):
            full = os.path.join(ddir, name)
            if os.path.isfile(full):
                z.write(full, "designs/" + name)
    fname = "compound-backup-%s.zip" % datetime.date.today().isoformat()
    return FileResponse(path, filename=fname, media_type="application/zip",
                        background=BackgroundTask(os.remove, path))


@app.get("/api/status")
def status():
    with db.connect() as con:
        queued = con.execute(
            "SELECT COUNT(*) AS c FROM designs WHERE status IN ('queued', 'generating')"
        ).fetchone()["c"]
    today = db.images_today()
    return {
        "today": today,
        "cap": worker.DAILY_CAP,
        "queued": queued,
        "paused": not pipeline.has_local() and today >= worker.DAILY_CAP,
        "local": pipeline.has_local(),
        "has_key": bool(db.get_setting("gemini_api_key")),
        "printify_ready": bool(
            db.get_setting("printify_api_token") and db.get_setting("printify_shop_id")
        ),
        "access_code": bool(db.get_setting("access_code")),
    }
