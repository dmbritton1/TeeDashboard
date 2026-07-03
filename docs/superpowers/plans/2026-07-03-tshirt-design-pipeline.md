# T-shirt Design Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local dashboard where the user pastes `phrase | filters` lines, Gemini generates t-shirt design candidates on the free tier (rate-limit-safe), the user approves/rejects them in a grid, approved designs are upscaled locally for print, and can be published to Printify/Etsy.

**Architecture:** Single FastAPI server at repo root with SQLite (stdlib), a background worker thread that drains a generation queue at free-tier pace, and one static vanilla-JS dashboard page. Image generation is one swappable function. Upscaling (Real-ESRGAN) and Printify publishing run as isolated modules.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, sqlite3 (stdlib), google-genai SDK (Gemini 2.5 Flash Image), py-real-esrgan + torch (upscale, added late), requests (Printify), vanilla HTML/JS.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-03-tshirt-design-pipeline-design.md`
- Gemini model id: `gemini-2.5-flash-image`
- Rate-limit prevention: `DAILY_CAP = 450` images/day (buffer under the ~500 free-tier cap), `SECONDS_BETWEEN = 31` seconds between generations (~2/min)
- Design status lifecycle: `queued → generating → pending → approved | rejected → published`, plus `failed` (with `error` text)
- SQLite via stdlib `sqlite3` only — no ORM
- Python files live at repo root (no package directory); frontend is one `static/index.html`, no build step
- API keys: stored in the `settings` DB table via the dashboard; environment variables (`GEMINI_API_KEY`, `PRINTIFY_API_TOKEN`, `PRINTIFY_SHOP_ID`) are the fallback
- Never commit secrets or generated artifacts: `.env`, `designs.db`, `designs/`, `weights/` are gitignored
- Virtualenv at `.venv`; run tests with `.venv/bin/pytest`, server with `.venv/bin/uvicorn main:app --port 8000`
- The user has NO Printify account yet — Printify code paths are verified only up to the "not configured" error; live publish is verified later by the user

---

### Task 1: Project setup + input parsing and prompt building

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces: `pipeline.parse_input(text: str) -> list[tuple[str, str]]` — list of `(phrase, filters)`; `filters` is a normalized comma-joined string, `""` if absent.
- Produces: `pipeline.build_prompt(phrase: str, filters: str) -> str`
- Produces: constants `pipeline.PROMPT_TEMPLATE`, `pipeline.GEMINI_MODEL = "gemini-2.5-flash-image"`

- [ ] **Step 1: Create requirements.txt and .gitignore**

`requirements.txt`:
```
fastapi
uvicorn[standard]
python-dotenv
google-genai
requests
pillow
pytest
```

`.gitignore`:
```
.venv/
__pycache__/
.env
designs.db
designs/
weights/
.pytest_cache/
```

- [ ] **Step 2: Create the venv and install**

Run: `python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt`
Expected: exits 0.

- [ ] **Step 3: Write the failing tests**

`tests/test_pipeline.py`:
```python
from pipeline import parse_input, build_prompt


def test_parse_basic():
    text = "funny fishing shirt | vintage, distressed, black shirt\nplant mom | retro 70s, floral\n"
    assert parse_input(text) == [
        ("funny fishing shirt", "vintage, distressed, black shirt"),
        ("plant mom", "retro 70s, floral"),
    ]


def test_parse_bare_phrase_and_blank_lines():
    assert parse_input("\ndog dad\n\n") == [("dog dad", "")]


def test_parse_strips_messy_whitespace():
    assert parse_input("  cat mom  |  cute ,  pastel  ") == [("cat mom", "cute, pastel")]


def test_parse_skips_empty_phrase():
    assert parse_input("| vintage") == []


def test_prompt_includes_phrase_and_filters():
    p = build_prompt("dog dad", "minimalist, line art")
    assert "dog dad" in p and "minimalist, line art" in p


def test_prompt_without_filters_has_no_style_clause():
    assert "Style:" not in build_prompt("dog dad", "")
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pipeline.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline'`

- [ ] **Step 5: Implement pipeline.py (parsing + prompt only; generation comes in Task 3)**

`pipeline.py`:
```python
"""Input parsing, prompt building, and image generation."""

GEMINI_MODEL = "gemini-2.5-flash-image"

PROMPT_TEMPLATE = (
    "Professional t-shirt graphic design: {phrase}. "
    "{style}Bold, high-contrast, visually striking artwork centered on a plain solid background. "
    "No shirt, no mockup, no watermark - just the artwork itself."
)


def parse_input(text: str) -> list[tuple[str, str]]:
    """Parse pasted 'phrase | filter1, filter2' lines into (phrase, filters) tuples."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        phrase, _, filters = line.partition("|")
        phrase = phrase.strip()
        if not phrase:
            continue
        filters = ", ".join(f.strip() for f in filters.split(",") if f.strip())
        items.append((phrase, filters))
    return items


def build_prompt(phrase: str, filters: str) -> str:
    style = f"Style: {filters}. " if filters else ""
    return PROMPT_TEMPLATE.format(phrase=phrase, style=style)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -q`
Expected: `6 passed`

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .gitignore pipeline.py tests/test_pipeline.py
git commit -m "feat: input parsing and prompt building"
```

---

### Task 2: SQLite layer (schema, settings with env fallback, daily usage counter)

**Files:**
- Create: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `db.DB_PATH` (module-level str, monkeypatchable in tests)
- Produces: `db.connect() -> sqlite3.Connection` (row_factory=Row, usable as context manager)
- Produces: `db.init()` — creates tables, idempotent
- Produces: `db.get_setting(key: str, default=None) -> str | None` — DB value first, else `os.environ[key.upper()]`, else default
- Produces: `db.set_setting(key: str, value: str)`
- Produces: `db.images_today() -> int`, `db.record_image()` — persistent per-day counter
- Table `designs`: `id, phrase, filters, file, print_file, status, error, created_at`

- [ ] **Step 1: Write the failing tests**

`tests/test_db.py`:
```python
import db


def setup_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()


def test_init_is_idempotent(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.init()  # second call must not raise


def test_usage_counter(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert db.images_today() == 0
    db.record_image()
    db.record_image()
    assert db.images_today() == 2


def test_settings_roundtrip_and_env_fallback(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert db.get_setting("gemini_api_key") is None
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert db.get_setting("gemini_api_key") == "env-key"
    db.set_setting("gemini_api_key", "db-key")
    assert db.get_setting("gemini_api_key") == "db-key"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Implement db.py**

`db.py`:
```python
"""SQLite storage: designs, settings, and the daily image-usage counter."""
import datetime as dt
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "designs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase TEXT NOT NULL,
    filters TEXT NOT NULL DEFAULT '',
    file TEXT,
    print_file TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, images INTEGER NOT NULL DEFAULT 0);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)


def get_setting(key: str, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row and row["value"]:
        return row["value"]
    return os.environ.get(key.upper(), default)


def set_setting(key: str, value: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _today() -> str:
    return dt.date.today().isoformat()


def images_today() -> int:
    with connect() as con:
        row = con.execute("SELECT images FROM usage WHERE day = ?", (_today(),)).fetchone()
    return row["images"] if row else 0


def record_image() -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO usage (day, images) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET images = images + 1",
            (_today(),),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_db.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: sqlite layer with settings and daily usage counter"
```

---

### Task 3: Gemini generation + rate-limited background worker

**Files:**
- Modify: `pipeline.py` (append `generate_image`)
- Create: `worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `db.connect/get_setting/images_today/record_image`, `pipeline.build_prompt`
- Produces: `pipeline.generate_image(prompt: str, api_key: str) -> bytes` (PNG bytes; raises on failure)
- Produces: `worker.DAILY_CAP = 450`, `worker.SECONDS_BETWEEN = 31`, `worker.DESIGNS_DIR`
- Produces: `worker.process_next() -> bool` (one queue item; False when idle/paused/keyless)
- Produces: `worker.start()` — launches the daemon thread (called by `main.py` in Task 4)

- [ ] **Step 1: Append generate_image to pipeline.py**

Append to `pipeline.py`:
```python
def generate_image(prompt: str, api_key: str) -> bytes:
    """Generate one PNG via Gemini. Swappable: replace this to use another model."""
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    for part in resp.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            return part.inline_data.data
    raise RuntimeError("Gemini returned no image (text: %s)" % (getattr(resp, "text", "") or "empty"))
```

- [ ] **Step 2: Write the failing tests**

`tests/test_worker.py`:
```python
import db
import worker


def setup_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(worker, "DESIGNS_DIR", str(tmp_path / "designs"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db.init()


def queue_one():
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('dog dad', '', 'queued')")


def test_idle_without_queued_rows(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "fake")
    assert worker.process_next() is False


def test_waits_without_api_key(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    queue_one()
    assert worker.process_next() is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM designs").fetchone()["status"] == "queued"


def test_respects_daily_cap(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "fake")
    queue_one()
    monkeypatch.setattr(db, "images_today", lambda: worker.DAILY_CAP)
    assert worker.process_next() is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM designs").fetchone()["status"] == "queued"


def test_generates_writes_file_and_counts(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "fake")
    monkeypatch.setattr(worker.pipeline, "generate_image", lambda prompt, key: b"fake-png")
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "pending"
    assert row["file"] == "designs/%d.png" % row["id"]
    assert db.images_today() == 1


def test_failure_marks_failed_with_error(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "fake")

    def boom(prompt, key):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(worker.pipeline, "generate_image", boom)
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "failed"
    assert "model exploded" in row["error"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_worker.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'worker'`

- [ ] **Step 4: Implement worker.py**

`worker.py`:
```python
"""Background queue worker: paces Gemini calls to stay inside the free tier."""
import os
import threading
import time

import db
import pipeline

DAILY_CAP = 450        # stop 50 short of the ~500/day free-tier cap
SECONDS_BETWEEN = 31   # ~2 images/min free-tier pace
DESIGNS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "designs")


def process_next() -> bool:
    """Generate one queued design. Returns True if work was attempted."""
    key = db.get_setting("gemini_api_key")
    if not key or db.images_today() >= DAILY_CAP:
        return False
    with db.connect() as con:
        row = con.execute(
            "SELECT * FROM designs WHERE status = 'queued' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return False
        con.execute("UPDATE designs SET status = 'generating' WHERE id = ?", (row["id"],))
    try:
        png = pipeline.generate_image(pipeline.build_prompt(row["phrase"], row["filters"]), key)
        os.makedirs(DESIGNS_DIR, exist_ok=True)
        with open(os.path.join(DESIGNS_DIR, "%d.png" % row["id"]), "wb") as f:
            f.write(png)
        db.record_image()
        with db.connect() as con:
            con.execute(
                "UPDATE designs SET status = 'pending', file = ?, error = NULL WHERE id = ?",
                ("designs/%d.png" % row["id"], row["id"]),
            )
    except Exception as e:
        msg = str(e)[:500]
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            # rate-limited despite pacing: requeue and back off, never fail the item
            with db.connect() as con:
                con.execute("UPDATE designs SET status = 'queued' WHERE id = ?", (row["id"],))
            time.sleep(60)
        else:
            with db.connect() as con:
                con.execute(
                    "UPDATE designs SET status = 'failed', error = ? WHERE id = ?",
                    (msg, row["id"]),
                )
    return True


def run() -> None:
    while True:
        try:
            worked = process_next()
        except Exception:
            worked = False  # never let the worker thread die
        time.sleep(SECONDS_BETWEEN if worked else 2)


def start() -> None:
    threading.Thread(target=run, daemon=True).start()
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `.venv/bin/pytest -q`
Expected: `14 passed` (6 pipeline + 3 db + 5 worker)

- [ ] **Step 6: Commit**

```bash
git add pipeline.py worker.py tests/test_worker.py
git commit -m "feat: gemini generation with rate-limited background worker"
```

---

### Task 4: FastAPI server and routes

**Files:**
- Create: `main.py`

**Interfaces:**
- Consumes: `db.*`, `pipeline.parse_input`, `worker.start/DAILY_CAP`
- Produces routes (all JSON under `/api`): `POST /api/generate {text, variations=2}`, `GET /api/designs`, `POST /api/designs/{id}/approve|reject|retry|regenerate|publish`, `GET/POST /api/settings`, `GET /api/status`
- Produces: `GET /` serves `static/index.html`; `/designs/*` serves generated images
- Produces: `_set_status(design_id, to, allowed)` helper reused by Task 6/7 modifications
- `publish` is a 501 stub until Task 7

- [ ] **Step 1: Implement main.py**

`main.py`:
```python
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
```

- [ ] **Step 2: Create a placeholder static/index.html so `/` doesn't 404**

`static/index.html` (replaced fully in Task 5):
```html
<!doctype html><html><body>Dashboard coming in Task 5</body></html>
```

- [ ] **Step 3: Verify the API works end to end (no key needed — queue only)**

Run (in background): `.venv/bin/uvicorn main:app --port 8000`
Then:
```bash
curl -s -X POST localhost:8000/api/generate -H 'Content-Type: application/json' \
  -d '{"text": "dog dad | minimalist, line art"}'
curl -s localhost:8000/api/status
curl -s localhost:8000/api/designs
```
Expected: `{"queued":2}`; status shows `"queued":2` (or 1–2 with one `generating`), `"has_key":false`, `"today":0`; designs list has 2 rows with status `queued` (they stay queued because no key is set — this proves the worker's key guard works live).

Also verify state guards:
```bash
curl -s -X POST localhost:8000/api/designs/1/approve
```
Expected: 409 `{"detail":"Design is not in a valid state for that action"}` (it's queued, not pending).

Stop the server. Delete the test DB so the user starts clean: `rm -f designs.db`

- [ ] **Step 4: Commit**

```bash
git add main.py static/index.html
git commit -m "feat: fastapi server with queue, review, and settings routes"
```

---

### Task 5: Dashboard UI

**Files:**
- Modify: `static/index.html` (full replacement of the Task 4 placeholder)

**Interfaces:**
- Consumes every `/api` route from Task 4 exactly as defined there.
- Card actions per status — pending: Approve/Reject/Regenerate; approved: Publish (+ "upscaling…"/"print-ready ✓" hint from `print_file`); failed and rejected: Retry/Re-queue; queued/generating: spinner placeholder.

- [ ] **Step 1: Replace static/index.html with the full dashboard**

`static/index.html`:
```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>T-Shirt Design Dashboard</title>
<style>
  body { font-family: -apple-system, sans-serif; margin: 0; background: #f5f5f4; color: #1c1917; }
  header { background: #1c1917; color: #fafaf9; padding: 12px 20px; display: flex; gap: 16px; align-items: center; }
  header h1 { font-size: 16px; margin: 0; flex: 1; }
  #statusbar { font-size: 13px; opacity: .9; }
  main { max-width: 1100px; margin: 0 auto; padding: 20px; }
  section.panel { background: #fff; border: 1px solid #e7e5e4; border-radius: 8px; padding: 16px; margin-bottom: 16px; }
  textarea { width: 100%; height: 110px; font: 13px/1.4 Menlo, monospace; box-sizing: border-box; }
  button { cursor: pointer; border: 1px solid #d6d3d1; background: #fff; border-radius: 6px; padding: 6px 12px; font-size: 13px; }
  button.primary { background: #1c1917; color: #fff; border-color: #1c1917; }
  .tabs { display: flex; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
  .tabs button.active { background: #1c1917; color: #fff; }
  #grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 14px; }
  .card { background: #fff; border: 1px solid #e7e5e4; border-radius: 8px; overflow: hidden; }
  .card img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; background: #eee; }
  .card .body { padding: 10px; font-size: 13px; }
  .card .filters { color: #78716c; font-size: 12px; margin-top: 2px; }
  .card .error { color: #b91c1c; font-size: 12px; margin-top: 4px; }
  .card .actions { display: flex; gap: 6px; padding: 0 10px 10px; flex-wrap: wrap; align-items: center; }
  .placeholder { display: flex; align-items: center; justify-content: center; aspect-ratio: 1; color: #a8a29e; font-size: 13px; background: #fafaf9; }
  input[type=password], input[type=text] { padding: 6px 8px; border: 1px solid #d6d3d1; border-radius: 6px; width: 260px; }
  .row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
  .hint { font-size: 12px; color: #78716c; }
</style>
</head>
<body>
<header>
  <h1>T-Shirt Design Dashboard</h1>
  <div id="statusbar">loading…</div>
</header>
<main>
  <section class="panel">
    <div class="row">
      <label>Gemini API key</label>
      <input type="password" id="gemini_key" placeholder="paste key from aistudio.google.com">
      <button onclick="saveSettings()">Save</button>
      <span class="hint" id="key_state"></span>
    </div>
    <div class="row">
      <label>Printify token</label>
      <input type="password" id="printify_token" placeholder="optional, for publishing">
      <label>Shop ID</label>
      <input type="text" id="printify_shop" placeholder="shop id" style="width:120px">
    </div>
  </section>
  <section class="panel">
    <textarea id="input" placeholder="funny fishing shirt | vintage, distressed, black shirt&#10;plant mom | retro 70s, floral"></textarea>
    <div class="row" style="margin-top:8px">
      <button class="primary" onclick="generate()">Generate designs</button>
      <span class="hint">2 variations per line · paced ~2/min to stay inside the free tier</span>
    </div>
  </section>
  <div class="tabs" id="tabs"></div>
  <div id="grid"></div>
</main>
<script>
const TABS = ["pending", "queued", "approved", "published", "failed", "rejected"];
let tab = "pending", designs = [];

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return r.json();
}
async function generate() {
  const text = document.getElementById("input").value;
  if (!text.trim()) return;
  try {
    await api("/api/generate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text})});
    document.getElementById("input").value = "";
  } catch (e) { alert(e.message); }
  refresh();
}
async function saveSettings() {
  const body = {
    gemini_api_key: document.getElementById("gemini_key").value,
    printify_api_token: document.getElementById("printify_token").value,
    printify_shop_id: document.getElementById("printify_shop").value,
  };
  try { await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}); }
  catch (e) { alert(e.message); }
  document.getElementById("gemini_key").value = "";
  document.getElementById("printify_token").value = "";
  refresh();
}
async function act(id, action) {
  try { await api(`/api/designs/${id}/${action}`, {method: "POST"}); }
  catch (e) { alert(e.message); }
  refresh();
}
function esc(s) {
  return (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function card(d) {
  const generating = d.status === "queued" || d.status === "generating";
  const img = d.file
    ? `<img src="/${d.file}" loading="lazy">`
    : `<div class="placeholder">${generating ? "generating…" : "no image"}</div>`;
  const buttons = {
    pending: `<button onclick="act(${d.id},'approve')">✓ Approve</button><button onclick="act(${d.id},'reject')">✕ Reject</button><button onclick="act(${d.id},'regenerate')">↻ Regenerate</button>`,
    approved: `<button onclick="act(${d.id},'publish')">Publish to Printify</button>` +
      (d.print_file ? '<span class="hint">print-ready ✓</span>' : '<span class="hint">upscaling…</span>'),
    failed: `<button onclick="act(${d.id},'retry')">Retry</button>`,
    rejected: `<button onclick="act(${d.id},'retry')">Re-queue</button>`,
  }[d.status] || "";
  return `<div class="card">${img}<div class="body"><b>${esc(d.phrase)}</b>` +
    `<div class="filters">${esc(d.filters)}</div>` +
    (d.error ? `<div class="error">${esc(d.error)}</div>` : "") +
    `</div><div class="actions">${buttons}</div></div>`;
}
function render() {
  const counts = {};
  designs.forEach(d => {
    const t = d.status === "generating" ? "queued" : d.status;
    counts[t] = (counts[t] || 0) + 1;
  });
  document.getElementById("tabs").innerHTML = TABS.map(t =>
    `<button class="${t === tab ? "active" : ""}" onclick="tab='${t}';render()">${t} (${counts[t] || 0})</button>`).join("");
  const shown = designs.filter(d => d.status === tab || (tab === "queued" && d.status === "generating"));
  document.getElementById("grid").innerHTML = shown.map(card).join("") || '<p class="hint">nothing here</p>';
}
async function refresh() {
  try {
    const [status, list] = await Promise.all([api("/api/status"), api("/api/designs")]);
    designs = list;
    document.getElementById("statusbar").textContent =
      `today: ${status.today}/${status.cap} images · queued: ${status.queued}` +
      (status.has_key ? "" : " · ⚠ no API key") +
      (status.paused ? " · daily cap reached — resumes tomorrow" : "");
    document.getElementById("key_state").textContent = status.has_key ? "key saved ✓" : "no key saved";
    render();
  } catch (e) { document.getElementById("statusbar").textContent = "server unreachable"; }
}
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
```

- [ ] **Step 2: Verify in the running app**

Run (in background): `.venv/bin/uvicorn main:app --port 8000`
Verify with the preview/browser tools or manually at `http://localhost:8000`:
1. Page loads, status bar shows `today: 0/450 images · queued: 0 · ⚠ no API key`.
2. Paste `dog dad | minimalist, line art` and click Generate → two cards appear under the **queued** tab with "generating…" placeholders (they stay queued without a key — correct).
3. Save a fake Gemini key (e.g. `test123`) via the settings field → status bar drops the "no API key" warning, `key saved ✓` appears. (The worker will then attempt generation and fail with an auth error → cards move to **failed** with an error message and a Retry button. This proves the whole loop live.)
4. Clean up: stop the server, `rm -f designs.db`.

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat: dashboard ui with tabs, settings, and live status bar"
```

---

### Task 6: Local Real-ESRGAN upscaling on approve

**Files:**
- Create: `upscale.py`
- Modify: `main.py` (approve route + import)
- Modify: `requirements.txt` (add torch + py-real-esrgan)

**Interfaces:**
- Consumes: `db.connect`, `main._set_status`
- Produces: `upscale.upscale(design_id: int, src_path: str)` — fire-and-forget background thread; writes `<src>_print.png` sibling file and sets `print_file` on the row; on failure stores `upscale failed: …` in `error` and leaves the design approved (publish falls back to the 1024px original).

- [ ] **Step 1: Add heavy deps and install**

Append to `requirements.txt`:
```
torch
py-real-esrgan
```

Run: `.venv/bin/pip install -q torch py-real-esrgan`
Expected: exits 0 (large download, several minutes).
NOTE: if `py-real-esrgan`'s import path differs from `py_real_esrgan.model` (verify with `.venv/bin/python -c "from py_real_esrgan.model import RealESRGAN; print('ok')"`), check `.venv/bin/pip show -f py-real-esrgan` for the actual module name and adjust the import in Step 2 — do not silently skip this.

- [ ] **Step 2: Implement upscale.py**

`upscale.py`:
```python
"""Local Real-ESRGAN 4x upscale: 1024px design -> ~4096px print file."""
import os
import threading

import db

_model = None
_lock = threading.Lock()  # ponytail: one upscale at a time on an 8GB machine

WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "RealESRGAN_x4.pth")


def _get_model():
    global _model
    if _model is None:
        import torch
        from py_real_esrgan.model import RealESRGAN

        device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        _model = RealESRGAN(device, scale=4)
        _model.load_weights(WEIGHTS, download=True)
    return _model


def upscale(design_id: int, src_path: str) -> None:
    """Fire-and-forget: upscale in a background thread, record print_file when done."""

    def job():
        with _lock:
            try:
                from PIL import Image

                img = Image.open(src_path).convert("RGB")
                result = _get_model().predict(img)
                out_path = src_path[: -len(".png")] + "_print.png"
                result.save(out_path)
                rel = os.path.join("designs", os.path.basename(out_path))
                with db.connect() as con:
                    con.execute(
                        "UPDATE designs SET print_file = ? WHERE id = ?", (rel, design_id)
                    )
            except Exception as e:
                # design stays approved; publish falls back to the 1024px original
                with db.connect() as con:
                    con.execute(
                        "UPDATE designs SET error = ? WHERE id = ?",
                        (("upscale failed: %s" % e)[:500], design_id),
                    )

    threading.Thread(target=job, daemon=True).start()
```

- [ ] **Step 3: Wire into the approve route**

In `main.py`, add `import upscale` next to the other local imports, then replace the `approve` function with:
```python
@app.post("/api/designs/{design_id}/approve")
def approve(design_id: int):
    _set_status(design_id, "approved", ("pending",))
    with db.connect() as con:
        row = con.execute("SELECT file FROM designs WHERE id = ?", (design_id,)).fetchone()
    if row and row["file"]:
        upscale.upscale(design_id, os.path.join(BASE, row["file"]))
    return {"ok": True}
```

- [ ] **Step 4: Verify with a real image**

```bash
mkdir -p designs
.venv/bin/python -c "
from PIL import Image
Image.new('RGB', (256, 256), 'navy').save('designs/999.png')"
.venv/bin/python -c "
import db, time, upscale
db.init()
with db.connect() as con:
    con.execute(\"INSERT INTO designs (id, phrase, status, file) VALUES (999, 'test', 'approved', 'designs/999.png')\")
upscale.upscale(999, 'designs/999.png')
time.sleep(180)
with db.connect() as con:
    print(dict(con.execute('SELECT print_file, error FROM designs WHERE id = 999').fetchone()))"
.venv/bin/python -c "from PIL import Image; print(Image.open('designs/999_print.png').size)"
```
Expected: first weights download happens automatically; final output `(1024, 1024)` (4× the 256px test input) and `print_file = 'designs/999_print.png'`, `error = None`. Adjust the sleep upward if the machine is slow — the check is the printed row, not the timing.
Clean up: `rm -f designs.db designs/999.png designs/999_print.png`

- [ ] **Step 5: Run all tests still pass**

Run: `.venv/bin/pytest -q`
Expected: `14 passed`

- [ ] **Step 6: Commit**

```bash
git add upscale.py main.py requirements.txt
git commit -m "feat: local real-esrgan upscale on approve"
```

---

### Task 7: Printify publishing

**Files:**
- Create: `printify.py`
- Modify: `main.py` (replace the 501 publish stub, add `import printify`)

**Interfaces:**
- Consumes: `db.get_setting("printify_api_token")`, `db.get_setting("printify_shop_id")`
- Produces: `printify.publish(design: dict) -> str` — uploads the image (print_file, else file), creates a Gildan 5000 tee product, publishes it to the connected Etsy shop, returns the Printify product id. Raises `requests.HTTPError`/`RuntimeError` on failure.
- CANNOT be live-tested yet (user has no Printify account). Verify only the not-configured guard.

- [ ] **Step 1: Implement printify.py**

`printify.py`:
```python
"""Printify: upload image, create t-shirt product, publish to the connected Etsy shop."""
import base64

import requests

import db

API = "https://api.printify.com/v1"
BLUEPRINT_ID = 6  # Unisex Heavy Cotton Tee (Gildan 5000)
COLORS = {"Black", "White"}
PRICE_CENTS = 2499  # $24.99 default; tune later per product


def _headers() -> dict:
    return {"Authorization": "Bearer %s" % db.get_setting("printify_api_token")}


def _get(path: str):
    r = requests.get(API + path, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, payload: dict, timeout: int = 60):
    r = requests.post(API + path, headers=_headers(), json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def publish(design: dict) -> str:
    shop_id = db.get_setting("printify_shop_id")
    file_path = design.get("print_file") or design["file"]

    with open(file_path, "rb") as f:
        contents = base64.b64encode(f.read()).decode()
    image_id = _post(
        "/uploads/images.json",
        {"file_name": "design-%s.png" % design["id"], "contents": contents},
        timeout=120,
    )["id"]

    providers = _get("/catalog/blueprints/%d/print_providers.json" % BLUEPRINT_ID)
    if not providers:
        raise RuntimeError("No print providers for blueprint %d" % BLUEPRINT_ID)
    pp_id = providers[0]["id"]

    all_variants = _get(
        "/catalog/blueprints/%d/print_providers/%d/variants.json" % (BLUEPRINT_ID, pp_id)
    )["variants"]
    variants = [v for v in all_variants if v["options"].get("color") in COLORS] or all_variants[:10]

    product = _post(
        "/shops/%s/products.json" % shop_id,
        {
            "title": design["phrase"].title() + " T-Shirt",
            "description": design["phrase"],
            "blueprint_id": BLUEPRINT_ID,
            "print_provider_id": pp_id,
            "variants": [
                {"id": v["id"], "price": PRICE_CENTS, "is_enabled": True} for v in variants
            ],
            "print_areas": [
                {
                    "variant_ids": [v["id"] for v in variants],
                    "placeholders": [
                        {
                            "position": "front",
                            "images": [
                                {"id": image_id, "x": 0.5, "y": 0.5, "scale": 1.0, "angle": 0}
                            ],
                        }
                    ],
                }
            ],
        },
    )
    product_id = product["id"]

    _post(
        "/shops/%s/products/%s/publish.json" % (shop_id, product_id),
        {"title": True, "description": True, "images": True, "variants": True, "tags": True},
    )
    return product_id
```

- [ ] **Step 2: Replace the publish stub in main.py**

Add `import printify` next to the other local imports, then replace the `publish` route with:
```python
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
    row = dict(row)
    if row["file"]:
        row["file"] = os.path.join(BASE, row["file"])
    if row["print_file"]:
        row["print_file"] = os.path.join(BASE, row["print_file"])
    try:
        product_id = printify.publish(row)
    except Exception as e:
        raise HTTPException(502, "Printify error: %s" % e)
    with db.connect() as con:
        con.execute(
            "UPDATE designs SET status = 'published', error = NULL WHERE id = ?", (design_id,)
        )
    return {"product_id": product_id}
```

- [ ] **Step 3: Verify the not-configured guard live**

Run (in background): `.venv/bin/uvicorn main:app --port 8000`
```bash
curl -s -X POST localhost:8000/api/generate -H 'Content-Type: application/json' -d '{"text": "guard test", "variations": 1}'
curl -s -X POST localhost:8000/api/designs/1/publish
```
Expected: publish returns 400 `{"detail":"Printify not configured - add your token and shop ID in settings"}` (the config guard fires before the status check by design — config is the more actionable error).
Stop server, clean up: `rm -f designs.db`

- [ ] **Step 4: All tests still pass**

Run: `.venv/bin/pytest -q`
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add printify.py main.py
git commit -m "feat: printify product creation and etsy publish"
```

---

### Task 8: README and final polish

**Files:**
- Create: `README.md`

**Interfaces:** none — documentation only.

- [ ] **Step 1: Write README.md**

`README.md`:
```markdown
# T-Shirt Design Pipeline

Paste Etsy search phrases + style filters, generate design candidates with
Gemini (free tier), review them in a dashboard, approve the keepers
(auto-upscaled locally for print), and publish to Printify -> Etsy.

## Run it

    .venv/bin/uvicorn main:app --port 8000

Open http://localhost:8000

First-time setup:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

## Configure

Paste keys in the dashboard settings panel (stored in the local SQLite db):

- **Gemini API key** - free, from https://aistudio.google.com (required)
- **Printify token + shop ID** - from Printify account settings once you
  have a Printify account connected to your Etsy shop (only needed to publish)

Environment variables `GEMINI_API_KEY`, `PRINTIFY_API_TOKEN`,
`PRINTIFY_SHOP_ID` (or a `.env` file) work as fallbacks.

## Input format

One design per line in the big textbox:

    funny fishing shirt | vintage, distressed, black shirt
    plant mom | retro 70s, floral, cream shirt
    dog dad

Left of `|` = the design concept. Right = optional comma-separated style
filters. 2 variations are generated per line.

## Rate limiting (built in)

Generation is paced at ~2 images/min and stops at 450 images/day to stay
inside Gemini's free tier. Big batches just take a while - paste the list,
walk away, come back to review. The status bar shows today's usage.

## Tests

    .venv/bin/pytest -q
```

- [ ] **Step 2: Full test suite + clean tree check**

Run: `.venv/bin/pytest -q && git status --porcelain`
Expected: `14 passed`; git status shows only `README.md` untracked.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: readme with setup and usage"
```
