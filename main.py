"""FastAPI server for the t-shirt design pipeline dashboard."""
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import pipeline
import worker

load_dotenv()
BASE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(BASE, "designs"), exist_ok=True)
db.init()
worker.start()

app = FastAPI(title="T-Shirt Design Pipeline")
app.mount("/designs", StaticFiles(directory=os.path.join(BASE, "designs")), name="designs")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE, "static", "index.html"))


class GenerateBody(BaseModel):
    text: str
    variations: int = 2


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
    return {"ok": True}


@app.post("/api/designs/{design_id}/reject")
def reject(design_id: int):
    _set_status(design_id, "rejected", ("pending",))
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


@app.post("/api/designs/{design_id}/publish")
def publish(design_id: int):
    raise HTTPException(501, "Publishing arrives in a later task")


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
        "paused": today >= worker.DAILY_CAP,
        "has_key": bool(db.get_setting("gemini_api_key")),
        "printify_ready": bool(
            db.get_setting("printify_api_token") and db.get_setting("printify_shop_id")
        ),
    }
