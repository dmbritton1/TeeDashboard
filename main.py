"""FastAPI server for the t-shirt design pipeline dashboard."""
import os

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import pipeline
import printify
import upscale
import worker

load_dotenv()
BASE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(BASE, "designs"), exist_ok=True)
db.init()
with db.connect() as con:
    # requeue rows orphaned by a shutdown mid-generation
    con.execute("UPDATE designs SET status = 'queued' WHERE status = 'generating'")
worker.start()

app = FastAPI(title="T-Shirt Design Pipeline")
app.mount("/designs", StaticFiles(directory=os.path.join(BASE, "designs")), name="designs")


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


class TestBody(BaseModel):
    text: str


class SettingsBody(BaseModel):
    gemini_api_key: str = ""
    printify_api_token: str = ""
    printify_shop_id: str = ""
    access_code: str = ""


@app.post("/api/generate")
def generate(body: GenerateBody, _gate: None = Depends(require_access_code)):
    items = pipeline.parse_input(body.text)
    if not items:
        raise HTTPException(400, "No valid lines found")
    if _queue_full():
        raise HTTPException(429, "Queue is full - try again shortly")
    with db.connect() as con:
        for phrase, filters in items:
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


@app.post("/api/designs/{design_id}/delete")
def delete_design(design_id: int, _gate: None = Depends(require_access_code)):
    with db.connect() as con:
        row = con.execute(
            "SELECT file, print_file FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Design not found")
        con.execute("DELETE FROM designs WHERE id = ?", (design_id,))
    for col in ("file", "print_file"):
        if row[col]:
            try:
                os.remove(os.path.join(BASE, row[col]))
            except OSError:
                pass  # best-effort; a missing file is fine
    return {"ok": True}


@app.get("/api/designs")
def list_designs():
    with db.connect() as con:
        rows = con.execute("SELECT * FROM designs ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


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
        row = con.execute("SELECT file FROM designs WHERE id = ?", (design_id,)).fetchone()
    if row and row["file"]:
        upscale.upscale(design_id, os.path.join(BASE, row["file"]))
    return {"ok": True}


@app.post("/api/designs/{design_id}/reject")
def reject(design_id: int, _gate: None = Depends(require_access_code)):
    _set_status(design_id, "rejected", ("pending",))
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
            "UPDATE designs SET status = 'published', error = NULL WHERE id = ?", (design_id,)
        )
    return {"product_id": product_id}


@app.get("/api/settings")
def get_settings():
    keys = ("gemini_api_key", "printify_api_token", "printify_shop_id")
    return {k: bool(db.get_setting(k)) for k in keys}


@app.post("/api/settings")
def save_settings(body: SettingsBody, _gate: None = Depends(require_access_code)):
    for k, v in body.model_dump().items():
        if v.strip():
            db.set_setting(k, v.strip())
    return {"ok": True}


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
