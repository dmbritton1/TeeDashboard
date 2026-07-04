"""FastAPI server for the t-shirt design pipeline dashboard."""
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


class GenerateBody(BaseModel):
    text: str
    variations: int = 2


class PatchBody(BaseModel):
    tags: str | None = None
    rating: int | None = None


class SettingsBody(BaseModel):
    gemini_api_key: str = ""
    printify_api_token: str = ""
    printify_shop_id: str = ""


@app.post("/api/generate")
def generate(body: GenerateBody):
    items = pipeline.parse_input(body.text)
    if not items:
        raise HTTPException(400, "No valid lines found")
    with db.connect() as con:
        for phrase, filters in items:
            for _ in range(body.variations):
                con.execute(
                    "INSERT INTO designs (phrase, filters, status) VALUES (?, ?, 'queued')",
                    (phrase, filters),
                )
    return {"queued": len(items) * body.variations}


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
def delete_design(design_id: int):
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (design_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Design not found")
        if row["status"] not in ("queued", "rejected", "failed"):
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
def approve(design_id: int):
    _set_status(design_id, "approved", ("pending",))
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = datetime('now') WHERE id = ?", (design_id,))
        row = con.execute("SELECT file FROM designs WHERE id = ?", (design_id,)).fetchone()
    if row and row["file"]:
        upscale.upscale(design_id, os.path.join(BASE, row["file"]))
    return {"ok": True}


@app.post("/api/designs/{design_id}/reject")
def reject(design_id: int):
    _set_status(design_id, "rejected", ("pending",))
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = datetime('now') WHERE id = ?", (design_id,))
    return {"ok": True}


@app.post("/api/designs/{design_id}/retry")
def retry(design_id: int):
    _set_status(design_id, "queued", ("failed", "rejected"))
    return {"ok": True}


@app.post("/api/designs/{design_id}/regenerate")
def regenerate(design_id: int):
    with db.connect() as con:
        row = con.execute(
            "SELECT phrase, filters FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Design not found")
        con.execute(
            "INSERT INTO designs (phrase, filters, status) VALUES (?, ?, 'queued')",
            (row["phrase"], row["filters"]),
        )
    return {"ok": True}


@app.post("/api/designs/{design_id}/unreview")
def unreview(design_id: int):
    _set_status(design_id, "pending", ("approved", "rejected"))
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = NULL WHERE id = ?", (design_id,))
    return {"ok": True}


@app.post("/api/designs/{design_id}/publish")
def publish(design_id: int):
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
def save_settings(body: SettingsBody):
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
    }
