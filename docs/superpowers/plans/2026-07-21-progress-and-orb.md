# Generation Progress Bar + Activity Orb Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show live per-step progress on each generating image, and a persistent corner orb that pulses with the queue count on every page.

**Architecture:** The FastAPI server and the FLUX worker share one process and one SQLite DB, so the worker reports progress by writing a `progress` column that the existing 3-second `/api/designs` poll reads back. The front end draws a gold bar that eases forward between polls, and a fixed top-right orb driven by the queue count already in `/api/status`.

**Tech Stack:** Python 3.10+, FastAPI, SQLite (`sqlite3`), diffusers/FLUX, vanilla JS + CSS (no framework, no new dependencies), pytest.

## Global Constraints

- **No Gemini.** Generation runs only when `pipeline.has_local()` is true (FLUX on a CUDA/ROCm GPU). The working tree already contains this FLUX-only refactor, uncommitted.
- **One design generates at a time** (single worker thread) → at most one active progress bar.
- **No new realtime channel.** Reuse the existing 3s polls of `/api/designs` and `/api/status` in `refresh()`.
- **`progress` is INTEGER 0–100, default 0.**
- **Step→percent mapping:** `round((step_index + 1) / (steps + 1) * 100)`; with `steps = 4` that is `20, 40, 60, 80`. The bar reaches 100 only when the row flips to `pending`.
- **No new dependencies.** Match existing style (printf `%` formatting, colors via CSS vars `--gold`, `--gold-leaf`, `--gold-soft`, `--vein-grey`).
- **Test commands:** `.venv/bin/python -m pytest tests/<file> -q`. Note `tests/test_api.py` imports `main` and is slow (minutes) on an 8GB Mac; `tests/test_pipeline.py`, `tests/test_db.py`, and `tests/test_worker.py` are fast.

---

### Task 1: Green the FLUX-only baseline

The uncommitted FLUX-only refactor left four red tests that reference the removed `pipeline.generate_image` and `main.test_gemini`. Fix them so the suite is green before adding anything.

**Files:**
- Modify: `tests/test_worker.py` (full rewrite)
- Modify: `tests/test_api.py` (delete two Gemini tests)
- Commit: also includes the already-modified `main.py`, `pipeline.py`, `worker.py`, `static/app.js`, `static/index.html` from the FLUX-only refactor.

**Interfaces:**
- Consumes: `worker.process_next() -> bool`, `pipeline.has_local()`, `pipeline.generate_image_local(prompt, on_step=None)`.
- Produces: a green suite; the FLUX-only worker semantics that later tasks build on.

- [ ] **Step 1: Rewrite `tests/test_worker.py` for FLUX-only**

Replace the entire file with:

```python
import db
import worker


def setup_tmp(tmp_path, monkeypatch, local=True):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(worker, "DESIGNS_DIR", str(tmp_path / "designs"))
    monkeypatch.setattr(worker.pipeline, "has_local", lambda: local)
    db.init()


def queue_one():
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('dog dad', '', 'queued')")


def test_idle_without_queued_rows(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert worker.process_next() is False


def test_skips_when_no_gpu(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=False)
    queue_one()
    assert worker.process_next() is False
    with db.connect() as con:
        assert con.execute("SELECT status FROM designs").fetchone()["status"] == "queued"


def test_generates_writes_file(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    monkeypatch.setattr(worker.pipeline, "generate_image_local", lambda prompt, on_step=None: b"fake-png")
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "pending"
    assert row["file"] == "designs/%d.png" % row["id"]


def test_failure_marks_failed_with_error(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)

    def boom(prompt, on_step=None):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(worker.pipeline, "generate_image_local", boom)
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "failed"
    assert "model exploded" in row["error"]
```

- [ ] **Step 2: Delete the two Gemini tests in `tests/test_api.py`**

Remove exactly these two functions (leave the `FakeResp` class — the Printify test still uses it):

```python
def test_test_gemini_no_key(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    out = main.test_gemini()
    assert out == {"ok": False, "message": "No Gemini key saved yet"}


def test_test_gemini_ok(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "k")
    monkeypatch.setattr(main.requests, "get", lambda *a, **kw: FakeResp(200))
    assert main.test_gemini()["ok"] is True
```

- [ ] **Step 3: Run the worker + pipeline suites**

Run: `.venv/bin/python -m pytest tests/test_worker.py tests/test_pipeline.py -q`
Expected: PASS (all green).

- [ ] **Step 4: Run the api + db suites**

Run: `.venv/bin/python -m pytest tests/test_api.py tests/test_db.py tests/test_access.py -q`
Expected: PASS (slow — imports `main`).

- [ ] **Step 5: Commit the whole FLUX-only change**

```bash
git add main.py pipeline.py worker.py static/app.js static/index.html tests/test_worker.py tests/test_api.py
git commit -m "refactor: FLUX-only generation, drop Gemini path and its tests"
```

---

### Task 2: Add the `progress` DB column

**Files:**
- Modify: `db.py:32-38` (MIGRATIONS tuple)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: a `progress INTEGER NOT NULL DEFAULT 0` column on `designs`, included automatically in `SELECT * FROM designs` (so `GET /api/designs` returns it with no endpoint change).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_db.py`:

```python
def test_progress_column_added_with_default_zero(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.init()  # run twice: must not raise
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
        assert "progress" in cols
        con.execute("INSERT INTO designs (phrase) VALUES ('x')")
        row = con.execute("SELECT progress FROM designs").fetchone()
    assert row["progress"] == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_progress_column_added_with_default_zero -q`
Expected: FAIL — `assert "progress" in cols` (column missing).

- [ ] **Step 3: Add the migration**

In `db.py`, add one line to the `MIGRATIONS` tuple (after the `test` entry):

```python
MIGRATIONS = (
    ("tags", "ALTER TABLE designs ADD COLUMN tags TEXT NOT NULL DEFAULT ''"),
    ("rating", "ALTER TABLE designs ADD COLUMN rating INTEGER NOT NULL DEFAULT 0"),
    ("product_id", "ALTER TABLE designs ADD COLUMN product_id TEXT"),
    ("reviewed_at", "ALTER TABLE designs ADD COLUMN reviewed_at TEXT"),
    ("test", "ALTER TABLE designs ADD COLUMN test INTEGER NOT NULL DEFAULT 0"),
    ("progress", "ALTER TABLE designs ADD COLUMN progress INTEGER NOT NULL DEFAULT 0"),
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_db.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: add progress column to designs"
```

---

### Task 3: Pipeline — step→percent mapping and the FLUX step callback

**Files:**
- Modify: `pipeline.py` (add `FLUX_STEPS`, `step_progress`, extend `generate_image_local`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces:
  - `pipeline.step_progress(step_index: int, steps: int) -> int`
  - `pipeline.generate_image_local(prompt: str, on_step=None) -> bytes` — `on_step(pct: int)` is called after each denoising step.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py` (and extend the import line at the top to `from pipeline import parse_input, build_prompt, step_progress`):

```python
def test_step_progress_maps_steps_to_reserved_percent():
    assert [step_progress(i, 4) for i in range(4)] == [20, 40, 60, 80]


def test_step_progress_monotonic_and_below_100():
    pct = [step_progress(i, 4) for i in range(4)]
    assert pct == sorted(pct)
    assert max(pct) < 100
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -q`
Expected: FAIL — `ImportError: cannot import name 'step_progress'`.

- [ ] **Step 3: Implement `step_progress` and the callback**

In `pipeline.py`, add `FLUX_STEPS` and `step_progress` just above `def generate_image_local`:

```python
FLUX_STEPS = 4


def step_progress(step_index: int, steps: int) -> int:
    """Percent to show after finishing step `step_index` (0-based) of `steps`.
    Reserves the top of the bar for the VAE decode that follows the loop."""
    return round((step_index + 1) / (steps + 1) * 100)
```

Then replace the body of `generate_image_local` with the callback-wired version:

```python
def generate_image_local(prompt: str, on_step=None) -> bytes:
    """Generate one PNG with FLUX.1-schnell on the local GPU (needs requirements-local.txt).
    on_step(pct) is called after each denoising step with an int 0-100."""
    global _flux
    import io

    if _flux is None:
        _flux = _build_flux()

    def _cb(pipe, step_index, timestep, kwargs):
        if on_step:
            on_step(step_progress(step_index, FLUX_STEPS))
        return kwargs

    img = _flux(
        prompt, num_inference_steps=FLUX_STEPS, guidance_scale=0.0,
        width=1024, height=1024, callback_on_step_end=_cb,
    ).images[0]
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
```

Note: `callback_on_step_end` is the modern diffusers hook; it receives `(pipeline, step_index, timestep, callback_kwargs)` and must return the kwargs dict. If the installed diffusers version rejects the arg, the implementer should confirm the signature in that version — but the pinned diffusers in `requirements-local.txt` supports it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -q`
Expected: PASS. (`generate_image_local` itself is not unit-tested — it needs a GPU; its callback is covered in Task 4 via a fake.)

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: report FLUX per-step progress via on_step callback"
```

---

### Task 4: Worker writes progress; server resets it on restart

**Files:**
- Modify: `worker.py` (reset on `generating`, pass `on_step`)
- Modify: `main.py` (startup requeue resets `progress`)
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `pipeline.generate_image_local(prompt, on_step=None)` from Task 3.
- Produces: rows whose `progress` advances during generation and is 0 on (re)queue.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_worker.py`:

```python
def test_reports_progress_via_callback(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)

    def fake(prompt, on_step=None):
        on_step(20)
        on_step(80)
        return b"fake-png"

    monkeypatch.setattr(worker.pipeline, "generate_image_local", fake)
    queue_one()
    assert worker.process_next() is True
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs").fetchone()
    assert row["status"] == "pending"
    assert row["progress"] == 80
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_worker.py::test_reports_progress_via_callback -q`
Expected: FAIL — `on_step` is never called (current `generate_image_local` fake gets a positional `prompt` only) / `progress` stays 0.

- [ ] **Step 3: Wire progress into the worker**

In `worker.py`, replace the `process_next` body so it resets progress when marking `generating` and passes an `on_step` writer:

```python
def process_next() -> bool:
    """Generate one queued design on the GPU. Returns True if work was attempted."""
    if not pipeline.has_local():
        return False  # no GPU here — leave designs queued instead of failing them
    with db.connect() as con:
        row = con.execute(
            # test images jump ahead so a scratch prompt isn't stuck behind a big batch
            "SELECT * FROM designs WHERE status = 'queued' ORDER BY test DESC, id LIMIT 1"
        ).fetchone()
        if not row:
            return False
        con.execute(
            "UPDATE designs SET status = 'generating', progress = 0 WHERE id = ?", (row["id"],)
        )
    try:
        # Test tab sends its text raw; the pipeline wraps its phrase in the t-shirt template
        prompt = row["phrase"] if row["test"] else pipeline.build_prompt(row["phrase"], row["filters"])

        def on_step(pct):
            with db.connect() as con:
                con.execute("UPDATE designs SET progress = ? WHERE id = ?", (pct, row["id"]))

        png = pipeline.generate_image_local(prompt, on_step=on_step)
        os.makedirs(DESIGNS_DIR, exist_ok=True)
        with open(os.path.join(DESIGNS_DIR, "%d.png" % row["id"]), "wb") as f:
            f.write(png)
        with db.connect() as con:
            con.execute(
                "UPDATE designs SET status = 'pending', file = ?, error = NULL WHERE id = ?",
                ("designs/%d.png" % row["id"], row["id"]),
            )
    except Exception as e:
        with db.connect() as con:
            con.execute(
                "UPDATE designs SET status = 'failed', error = ? WHERE id = ?",
                (str(e)[:500], row["id"]),
            )
    return True
```

- [ ] **Step 4: Reset progress on the startup requeue**

In `main.py`, the startup block currently reads:

```python
with db.connect() as con:
    # requeue rows orphaned by a shutdown mid-generation
    con.execute("UPDATE designs SET status = 'queued' WHERE status = 'generating'")
```

Change the UPDATE to also zero the progress:

```python
    con.execute("UPDATE designs SET status = 'queued', progress = 0 WHERE status = 'generating'")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_worker.py -q`
Expected: PASS (all worker tests).

- [ ] **Step 6: Commit**

```bash
git add worker.py main.py tests/test_worker.py
git commit -m "feat: worker records per-step progress; reset on requeue"
```

---

### Task 5: Front-end progress bar with gentle creep

No JS test framework exists in this repo, so this task uses **browser verification** via the preview tools instead of an automated test.

**Files:**
- Modify: `static/app.js` (`progressBar`/`barVisual`/`creepTick` helpers, `card`, `testCard`)
- Modify: `static/styles.css` (progress bar styles)

**Interfaces:**
- Consumes: `design.progress` (int) from `GET /api/designs`.
- Produces: a `.progress > .bar[data-id]` element inside the working placeholder of the actively-generating card.

- [ ] **Step 1: Add the bar helpers and creep loop to `static/app.js`**

Insert these helpers just above `function card(d, i) {` (near line 290):

```javascript
// Only the actively-generating design shows a bar; it eases forward between the
// 3s polls, then snaps to the real value as each FLUX step lands.
let creepId = null, creepVal = 0;
function barVisual(d) {
  return d.id === creepId ? creepVal : (d.progress || 0);
}
function progressBar(d) {
  return `<div class="progress"><div class="bar" data-id="${d.id}" style="width:${Math.max(2, barVisual(d))}%"></div></div>`;
}
function creepTick() {
  const active = designs.find(d => d.status === "generating");
  if (!active) { creepId = null; creepVal = 0; return; }
  if (active.id !== creepId) { creepId = active.id; creepVal = active.progress || 0; }
  const target = Math.min((active.progress || 0) + 18, 96);   // lead ahead, capped
  creepVal = Math.max(creepVal, Math.min(target, creepVal + 0.4));  // monotonic, always drifting up
  const bar = document.querySelector(`.bar[data-id="${creepId}"]`);
  if (bar) bar.style.width = Math.max(2, creepVal) + "%";
}
setInterval(creepTick, 120);
```

- [ ] **Step 2: Render the bar in `card` (only while generating)**

In `function card(d, i)`, change the placeholder line from:

```javascript
    : `<div class="placeholder ${generating ? "working" : ""}">${generating ? "in press…" : "no image"}</div>`;
```

to:

```javascript
    : `<div class="placeholder ${generating ? "working" : ""}">${generating ? "in press…" + (d.status === "generating" ? progressBar(d) : "") : "no image"}</div>`;
```

- [ ] **Step 3: Render the bar in `testCard` (only while generating)**

In `function testCard(d, i)`, change the placeholder line from:

```javascript
    : `<div class="placeholder ${generating ? "working" : ""}">${generating ? "in press…" : (d.error ? "failed" : "no image")}</div>`;
```

to:

```javascript
    : `<div class="placeholder ${generating ? "working" : ""}">${generating ? "in press…" + (d.status === "generating" ? progressBar(d) : "") : (d.error ? "failed" : "no image")}</div>`;
```

- [ ] **Step 4: Add the bar styles to `static/styles.css`**

Append after the `.placeholder.working` / `@keyframes sheen` block (around line 198):

```css
.placeholder.working { position: relative; }
.progress { position: absolute; left: 0; right: 0; bottom: 0; height: 4px; background: rgba(0, 0, 0, 0.45); }
.progress .bar {
  height: 100%;
  background: linear-gradient(90deg, var(--gold), var(--gold-leaf));
  box-shadow: 0 0 8px var(--gold);
  transition: width 0.3s linear;
}
```

- [ ] **Step 5: Verify in the browser (no GPU needed for this check)**

Start the dashboard preview (`preview_start` with `{name: "dashboard"}`), then in the preview tab run this to simulate a generating row and confirm the bar renders and creeps:

```javascript
// via javascript_tool in the preview tab
designs = [{id: 999, phrase: "creep test", filters: "", status: "generating", progress: 40, test: 0}];
render();
document.querySelector('.bar[data-id="999"]') ? "bar present" : "MISSING";
```

Expected: `"bar present"`, and over ~2s its inline `width` grows past 40% toward ~58% (creep). Also confirm `read_console_messages` shows no errors. On a real GPU machine, queue a batch and watch the bar advance in 20/40/60/80 steps with smooth creep between.

- [ ] **Step 6: Commit**

```bash
git add static/app.js static/styles.css
git commit -m "feat: gold progress bar with gentle creep on generating images"
```

---

### Task 6: Persistent activity orb

Browser-verified, like Task 5.

**Files:**
- Modify: `static/index.html` (orb element)
- Modify: `static/styles.css` (orb styles)
- Modify: `static/app.js` (`refresh` updates the orb)

**Interfaces:**
- Consumes: `status.queued` from `GET /api/status`.
- Produces: `#orb` (fixed, top-right) that gains `.live` and shows the count while the queue is non-empty.

- [ ] **Step 1: Add the orb element to `static/index.html`**

Immediately after the `<div class="app">` line (line 43), add:

```html
  <div id="orb" class="orb" title="Generation activity"><span id="orb_count" class="orb-count"></span></div>
```

- [ ] **Step 2: Add orb styles to `static/styles.css`**

Append at the end of the file (reuses the existing `@keyframes pulse`):

```css
.orb {
  position: fixed; top: 16px; right: 18px; z-index: 60;
  width: 14px; height: 14px; border-radius: 50%;
  background: var(--vein-grey); transition: background 0.4s;
}
.orb.live {
  background: var(--gold-soft);
  box-shadow: 0 0 16px 3px var(--gold);
  animation: pulse 2.4s ease-in-out infinite;
}
.orb .orb-count {
  position: absolute; top: 17px; right: 0; white-space: nowrap;
  font-family: var(--mono); font-size: 10px; color: var(--gold-soft);
}
```

- [ ] **Step 3: Drive the orb from `refresh()` in `static/app.js`**

Find this line in `refresh()` (around line 618):

```javascript
    document.querySelector("#statusbar .dot").classList.toggle("live", status.queued > 0);
```

Immediately after it, add:

```javascript
    const orb = document.getElementById("orb");
    orb.classList.toggle("live", status.queued > 0);
    document.getElementById("orb_count").textContent = status.queued > 0 ? status.queued : "";
```

- [ ] **Step 4: Verify in the browser**

With the preview running, queue a design (`POST /api/generate`) so the queue is non-empty. Then:

```javascript
// via javascript_tool in the preview tab, after a refresh cycle
JSON.stringify({
  live: document.getElementById("orb").classList.contains("live"),
  count: document.getElementById("orb_count").textContent
});
```

Expected: `{"live": true, "count": "1"}` (or higher). Navigate to `#library`, `#settings`, etc. and confirm the orb stays visible and pulsing on every view. Confirm `read_console_messages` shows no errors. Delete the test design afterward.

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/styles.css static/app.js
git commit -m "feat: persistent activity orb with live queue count"
```

---

## Self-Review

**Spec coverage:**
- Real step-based progress → Tasks 3, 4 (`step_progress`, `on_step`, worker writes). ✓
- Fill to ~80%, 100% only on landing → `step_progress` mapping + front end snapping (Task 5, bar removed when card leaves `generating`). ✓
- Gentle front-end creep → Task 5 `creepTick`. ✓
- New `progress` column via migration; reset on generating and on startup requeue → Tasks 2, 4. ✓
- `/api/designs` returns progress with no shape change → Task 2 (SELECT *). ✓
- Orb persistent, top-right, pulse + queue count, idle when empty → Task 6. ✓
- Non-GPU inert; single active bar → guaranteed by `has_local()` gate and single worker thread; asserted indirectly by Task 1 `test_skips_when_no_gpu`. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; every test step shows the assertion. ✓

**Type consistency:** `step_progress(step_index, steps)`, `generate_image_local(prompt, on_step=None)`, `on_step(pct)`, `progress` column, `barVisual`/`progressBar`/`creepTick`, `#orb`/`#orb_count` — names match across tasks. ✓
