# Four-Tab Dashboard Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the t-shirt dashboard into four hash-routed tabs (Dashboard, Add designs, Library, Settings) with stats/charts, CSV import, tag/rating search, and workflow upgrades, per the approved spec.

**Architecture:** Same FastAPI + SQLite + vanilla-JS stack. Client computes all stats from the already-polled `/api/designs` list. CSV parsing and duplicate detection happen in the browser. Four new columns on `designs`; a handful of small endpoints. Static frontend splits into `index.html` / `styles.css` / `app.js`.

**Tech Stack:** Python 3.12, FastAPI, SQLite (stdlib sqlite3), pytest, vanilla JS/CSS, hand-rolled inline SVG charts. **No new dependencies, no build step.**

**Spec:** `docs/superpowers/specs/2026-07-04-dashboard-reorg-design.md` — read it first.

## Global Constraints

- No new Python or JS dependencies; stdlib + already-installed packages only.
- Visual language is fixed: near-black `#14120f`, ivory `#e9e5dc`, gold `#c0913a` / leaf `#e9d08c`, Italiana display + Outfit body, CSS variables already defined in the current `<style>` block (`--marble`, `--slab`, `--mist`, `--ivory`, `--stone`, `--gold`, `--gold-leaf`, `--gold-soft`, `--gold-pale`, `--clay`, `--mono`). New UI reuses these tokens; never hardcode new colors.
- Statuses are exactly: `queued`, `generating`, `pending`, `approved`, `published`, `failed`, `rejected`.
- All user-facing copy is sentence case, plain verbs, no jargon (owner is non-technical).
- Frontend tests are manual browser checks (no JS test framework); every frontend task ends with explicit verification steps. Backend tasks are TDD with pytest.
- Run tests with: `.venv/bin/python -m pytest tests/ -q` from the repo root.
- The dev server for manual checks: `.venv/bin/uvicorn main:app --port 8000` (or the Claude preview launch config).
- Commit after every task with the message given in the task.

## File Structure

- `static/index.html` — shell only: fonts, marble SVG, sidebar (brand, 4 nav links, status), four `<section>` view containers, `<script src>`/`<link>` tags.
- `static/styles.css` — all CSS (moved from the old inline `<style>` + new component styles per task).
- `static/app.js` — all JS: router, polling, per-view render functions, stats, CSV, lightbox, keyboard mode.
- `db.py` — idempotent column migrations added to `init()`.
- `main.py` — new/changed endpoints (PATCH, DELETE, unreview, tests, export, backup, prompt_template, product_id capture).
- `tests/test_db.py` — migration test.
- `tests/test_api.py` — new file, endpoint tests via direct function calls (no HTTP client).

**Import-time gotcha:** `main.py` calls `worker.start()` and `db.init()` at import. Tests must patch `db.DB_PATH` and `worker.start` **before** importing/reloading `main` (helper given in Task 2).

---

# Stage 1 — Shell reorganization (no new features)

### Task 1: Split static files and add four hash-routed views

**Files:**
- Create: `static/styles.css`
- Create: `static/app.js`
- Rewrite: `static/index.html`

**Interfaces:**
- Produces: `showView()` router; view container ids `view-dashboard`, `view-add`, `view-library`, `view-settings`; globals `designs`, `stat`, `render()`, `refresh()`, `api()`, `esc()`, `tagsOf(d)` — later tasks hook into these exact names.
- Produces: per-view render hooks called from `render()`: `renderDashboard()`, `renderLibrary()` (stubs now, filled by later stages).

- [ ] **Step 1: Create `static/styles.css`**

Copy the entire contents of the `<style>` element in the current `static/index.html` (everything between `<style>` and `</style>`) into `static/styles.css` verbatim. Then apply these edits inside the new file:

Replace the sidebar `#tabs`/`.plaque` block (from `.nav-label {` through `.plaque.active .count {...}`) with nav links for the four views plus stage chips used inside the Dashboard:

```css
.nav-label {
  font-size: 9px; font-weight: 500; letter-spacing: 0.34em; text-transform: uppercase;
  color: var(--stone); padding: 22px 22px 10px;
}
#nav { display: flex; flex-direction: column; }
.navlink {
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
  width: 100%; padding: 11px 22px 11px 20px;
  border: none; border-left: 2px solid transparent; border-radius: 0;
  background: none; color: var(--stone); text-align: left; cursor: pointer;
  font-family: "Italiana", serif; font-size: 15px; letter-spacing: 0.12em; text-transform: uppercase;
  text-decoration: none;
  transition: background 0.2s, color 0.2s, border-color 0.2s;
}
.navlink:hover { color: var(--ivory); }
.navlink.active {
  color: var(--ivory); border-left-color: var(--gold);
  background: linear-gradient(90deg, rgba(211, 177, 101, 0.1), transparent 75%);
}
.navlink .badge { font-family: var(--mono); font-size: 10px; color: var(--vein-grey); }
.navlink.active .badge { color: var(--gold-soft); }

/* pipeline stage chips inside the Dashboard view */
#tabs { display: flex; gap: 4px 14px; flex-wrap: wrap; margin: 4px 0 18px; border-bottom: 1px solid var(--mist); }
.plaque {
  display: flex; align-items: baseline; gap: 8px;
  padding: 10px 4px 12px; border: none; border-bottom: 2px solid transparent; border-radius: 0;
  background: none; color: var(--stone); cursor: pointer;
  transition: color 0.2s, border-color 0.2s;
}
.plaque .name { font-family: "Italiana", serif; font-size: 15px; letter-spacing: 0.12em; text-transform: uppercase; }
.plaque .count { font-family: var(--mono); font-size: 10px; color: var(--vein-grey); }
.plaque:hover:not(:disabled) { color: var(--ivory); }
.plaque.active { color: var(--ivory); border-bottom-color: var(--gold); }
.plaque.active .count { color: var(--gold-soft); }

section[hidden] { display: none !important; }
```

In the `@media (max-width: 860px)` block, replace the `#tabs { flex-direction: row; ... }` and `.plaque { ... }` and `.plaque.active { ... }` rules with:

```css
  #nav { flex-direction: row; flex-wrap: wrap; justify-content: center; }
  .navlink { width: auto; padding: 10px 12px 12px; border-left: none; border-bottom: 2px solid transparent; }
  .navlink.active { border-bottom-color: var(--gold); background: none; }
```

- [ ] **Step 2: Create `static/app.js`**

Move the entire contents of the `<script>` element from the current `static/index.html` into `static/app.js` verbatim, then apply these changes:

Add at the top (before `const STAGES`):

```js
const VIEWS = ["dashboard", "add", "library", "settings"];
function currentView() {
  const h = location.hash.replace("#", "");
  return VIEWS.includes(h) ? h : "dashboard";
}
function showView() {
  const v = currentView();
  VIEWS.forEach(name => {
    document.getElementById("view-" + name).hidden = name !== v;
    document.querySelector(`.navlink[data-view="${name}"]`).classList.toggle("active", name === v);
  });
  render();
}
window.addEventListener("hashchange", showView);

// effective tags: style text split on commas ∪ manual tags (lowercased)
function tagsOf(d) {
  const split = s => (s || "").split(",").map(x => x.trim().toLowerCase()).filter(Boolean);
  return [...new Set([...split(d.filters), ...split(d.tags)])];
}

function renderDashboard() {} // filled in Stage 3
function renderLibrary() {}   // filled in Stage 5
```

In `render()`, add these two lines immediately before the existing `const shown = ...` line:

```js
  renderDashboard();
  renderLibrary();
```

At the bottom, replace the bare `refresh();` + `setInterval(refresh, 3000);` with:

```js
showView();
refresh();
setInterval(refresh, 3000);
```

- [ ] **Step 3: Rewrite `static/index.html`**

Replace the whole file with this shell (the `<svg id="marble">` block is copied **unchanged** from the current file — keep its three filters and three rects exactly as they are):

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Atelier — T-Shirt Design House</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Italiana&family=Outfit:wght@300;400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/styles.css">
</head>
<body>

<!-- ⟨marble SVG block: copy verbatim from current index.html⟩ -->

<div class="app">
  <aside>
    <div class="brand">
      <p class="eyebrow">T-shirt design house</p>
      <h1>The Atelier</h1>
      <div class="rule" aria-hidden="true"><span class="gem"></span></div>
    </div>
    <div class="nav-label">Menu</div>
    <nav id="nav" aria-label="Sections">
      <a class="navlink" data-view="dashboard" href="#dashboard"><span>Dashboard</span><span class="badge" id="badge_pending"></span></a>
      <a class="navlink" data-view="add" href="#add"><span>Add designs</span></a>
      <a class="navlink" data-view="library" href="#library"><span>Library</span></a>
      <a class="navlink" data-view="settings" href="#settings"><span>Settings</span></a>
    </nav>
    <div class="side-foot">
      <div id="statusbar"><span class="dot"></span><span id="status_text">loading…</span></div>
    </div>
  </aside>

  <main>
    <section id="view-dashboard">
      <div class="workhead">
        <h2 id="page_title">To review</h2>
        <span id="page_count"></span>
      </div>
      <nav id="tabs" aria-label="Pipeline stages"></nav>
      <div id="grid"></div>
    </section>

    <section id="view-add" hidden>
      <section class="panel">
        <div class="panel-label">Commission ideas — one per line: phrase | style</div>
        <textarea id="input" placeholder="funny fishing shirt | vintage, distressed, black shirt&#10;plant mom | retro 70s, floral"></textarea>
        <div class="row">
          <button class="gilt" onclick="generate()">Generate designs</button>
          <span class="hint">2 variations per line · paced ~2/min to stay inside the free tier</span>
        </div>
      </section>
    </section>

    <section id="view-library" hidden>
      <div class="workhead"><h2>Library</h2><span id="lib_count"></span></div>
      <div id="lib_grid"></div>
    </section>

    <section id="view-settings" hidden>
      <section class="panel">
        <div class="panel-label">Connections</div>
        <div class="row">
          <label>Gemini API key</label>
          <input type="password" id="gemini_key" placeholder="paste key from aistudio.google.com">
          <button onclick="saveSettings()">Save</button>
          <span class="hint" id="key_state"></span>
        </div>
        <div class="row">
          <label>Printify token</label>
          <input type="password" id="printify_token" placeholder="optional, for publishing">
        </div>
        <div class="row">
          <label>Shop ID</label>
          <input type="text" id="printify_shop" placeholder="shop id" style="width:140px">
        </div>
      </section>
    </section>
  </main>
</div>

<script src="/static/app.js"></script>
</body>
</html>
```

Note the settings `details` drawer became a plain always-open panel inside the Settings view (delete the `details.panel` markup; its CSS rules can stay, unused, until Stage 7 cleans up — or remove them now).

`/static/...` URLs require a static mount. Add to `main.py` after the existing `app.mount("/designs", ...)` line:

```python
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")
```

Also add a minimal Library placeholder so the tab isn't empty — in `app.js` replace the `renderLibrary` stub body:

```js
function renderLibrary() {
  document.getElementById("lib_count").textContent =
    designs.length === 1 ? "1 design" : `${designs.length} designs`;
  document.getElementById("lib_grid").innerHTML =
    designs.map(card).join("") ||
    `<div class="empty"><span class="fleuron">❦</span>Nothing here yet.</div>`;
}
```

and give `#lib_grid` the same grid styling — in `styles.css` change the `#grid {` selector line to `#grid, #lib_grid {`.

- [ ] **Step 4: Verify in browser**

Start the server, open the app:
- Four nav links in sidebar; `#dashboard` shows stage chips + review grid; `#add` shows the composer; `#library` shows all designs; `#settings` shows the three inputs.
- Hash routing: refresh on `#library` stays on Library; back button returns to prior tab.
- Status pill still updates; generating still works from the Add tab; approve/reject still work on Dashboard.
- Mobile width (≤860px): nav goes horizontal, everything stacks.
- Browser console: no errors.

- [ ] **Step 5: Run backend tests (must stay green)**

Run: `.venv/bin/python -m pytest tests/ -q` — Expected: all pass (no backend changes besides the static mount).

- [ ] **Step 6: Commit**

```bash
git add static/ main.py
git commit -m "feat: four-tab shell - split static files, hash routing"
```

---

# Stage 2 — Backend data layer

### Task 2: Schema migrations (tags, rating, product_id, reviewed_at)

**Files:**
- Modify: `db.py` (`init()`)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `designs` columns `tags TEXT NOT NULL DEFAULT ''`, `rating INTEGER NOT NULL DEFAULT 0`, `product_id TEXT`, `reviewed_at TEXT` — all later tasks rely on these existing.

- [ ] **Step 1: Write the failing test** — append to `tests/test_db.py`:

```python
def test_migrations_add_columns_idempotently(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.init()  # run twice: must not raise
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
    assert {"tags", "rating", "product_id", "reviewed_at"} <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_db.py::test_migrations_add_columns_idempotently -q`
Expected: FAIL — missing columns.

- [ ] **Step 3: Implement** — in `db.py`, add after `SCHEMA`:

```python
MIGRATIONS = (
    ("tags", "ALTER TABLE designs ADD COLUMN tags TEXT NOT NULL DEFAULT ''"),
    ("rating", "ALTER TABLE designs ADD COLUMN rating INTEGER NOT NULL DEFAULT 0"),
    ("product_id", "ALTER TABLE designs ADD COLUMN product_id TEXT"),
    ("reviewed_at", "ALTER TABLE designs ADD COLUMN reviewed_at TEXT"),
)
```

and change `init()` to:

```python
def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
        for col, stmt in MIGRATIONS:
            if col not in cols:
                con.execute(stmt)
```

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/ -q` — Expected: all pass.
- [ ] **Step 5: Commit** — `git add db.py tests/test_db.py && git commit -m "feat: schema migrations for tags, rating, product_id, reviewed_at"`

### Task 3: reviewed_at on approve/reject + unreview endpoint

**Files:**
- Modify: `main.py`
- Test: `tests/test_api.py` (create)

**Interfaces:**
- Consumes: columns from Task 2.
- Produces: `POST /api/designs/{id}/unreview` (function `main.unreview(design_id)`); `approve`/`reject` set `reviewed_at`; test helper `load_main(tmp_path, monkeypatch)` reused by Tasks 4–6 and Stage 7.

- [ ] **Step 1: Write the failing tests** — create `tests/test_api.py`:

```python
"""Endpoint tests by direct function call (no HTTP client needed)."""
import importlib

import pytest
from fastapi import HTTPException

import db
import worker


def load_main(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(worker, "start", lambda: None)
    import main
    main = importlib.reload(main)
    monkeypatch.setattr(main, "BASE", str(tmp_path))
    return main


def insert(status="pending", **kw):
    row = {"phrase": "dog dad", "filters": "vintage", "status": status, **kw}
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO designs (%s) VALUES (%s)"
            % (", ".join(row), ", ".join("?" * len(row))),
            tuple(row.values()),
        )
        return cur.lastrowid


def test_approve_sets_reviewed_at(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    main.approve(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "approved" and row["reviewed_at"]


def test_unreview_returns_to_pending(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("rejected", reviewed_at="2026-07-01 00:00:00")
    main.unreview(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "pending" and row["reviewed_at"] is None


def test_unreview_guards_status(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("queued")
    with pytest.raises(HTTPException) as e:
        main.unreview(did)
    assert e.value.status_code == 409
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_api.py -q`
Expected: FAIL — `main` has no attribute `unreview`; approve test fails on `reviewed_at` NULL.

- [ ] **Step 3: Implement** — in `main.py`:

In `approve()` and `reject()`, add a `reviewed_at` stamp right after the `_set_status(...)` call (both functions):

```python
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = datetime('now') WHERE id = ?", (design_id,))
```

(In `approve` merge it into the existing `with db.connect() as con:` block that fetches the file.)

Add the endpoint after `regenerate`:

```python
@app.post("/api/designs/{design_id}/unreview")
def unreview(design_id: int):
    _set_status(design_id, "pending", ("approved", "rejected"))
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = NULL WHERE id = ?", (design_id,))
    return {"ok": True}
```

Note: `upscale.upscale` in `approve` spawns real work — it is fine in tests because the inserted row has `file=None`, so the `if row and row["file"]:` guard skips it.

- [ ] **Step 4: Run tests** — `.venv/bin/python -m pytest tests/ -q` — Expected: all pass.
- [ ] **Step 5: Commit** — `git add main.py tests/test_api.py && git commit -m "feat: reviewed_at stamps and unreview endpoint"`

### Task 4: PATCH endpoint for tags and rating

**Files:**
- Modify: `main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `PATCH /api/designs/{id}` accepting `{"tags": str | null, "rating": int | null}`; function `main.patch_design(design_id, body)` with `body: main.PatchBody`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`:

```python
def test_patch_tags_and_rating(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    main.patch_design(did, main.PatchBody(tags="funny, dog", rating=9))
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["tags"] == "funny, dog"
    assert row["rating"] == 5  # clamped


def test_patch_missing_design_404(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.patch_design(999, main.PatchBody(rating=3))
    assert e.value.status_code == 404


def test_patch_empty_body_400(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    with pytest.raises(HTTPException) as e:
        main.patch_design(did, main.PatchBody())
    assert e.value.status_code == 400
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/python -m pytest tests/test_api.py -q` — Expected: FAIL, no `PatchBody`.

- [ ] **Step 3: Implement** — in `main.py`, add near the other body models:

```python
class PatchBody(BaseModel):
    tags: str | None = None
    rating: int | None = None
```

and the endpoint:

```python
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
```

- [ ] **Step 4: Run tests** — all pass. **Step 5: Commit** — `git add main.py tests/test_api.py && git commit -m "feat: PATCH designs for tags and rating"`

### Task 5: DELETE endpoint (cancel queued / delete rejected+failed)

**Files:**
- Modify: `main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `DELETE /api/designs/{id}` — function `main.delete_design(design_id)`; allowed statuses `queued`, `rejected`, `failed`; removes `file`/`print_file` from disk.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`:

```python
def test_delete_rejected_removes_row_and_files(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    img = tmp_path / "designs" / "9.png"
    img.parent.mkdir(exist_ok=True)
    img.write_bytes(b"png")
    did = insert("rejected", file="designs/9.png")
    main.delete_design(did)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM designs").fetchone()["c"] == 0
    assert not img.exists()


def test_delete_guards_status(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("approved")
    with pytest.raises(HTTPException) as e:
        main.delete_design(did)
    assert e.value.status_code == 409


def test_delete_missing_404(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.delete_design(1)
    assert e.value.status_code == 404
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL, no `delete_design`.

- [ ] **Step 3: Implement** — add to `main.py`:

```python
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
```

- [ ] **Step 4: Run tests** — all pass. **Step 5: Commit** — `git add main.py tests/test_api.py && git commit -m "feat: DELETE endpoint for queued/rejected/failed designs"`

### Task 6: Persist product_id on publish + prompt_template setting

**Files:**
- Modify: `main.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `publish` stores `product_id`; `GET /api/settings` returns `prompt_template` full text (default `main.DEFAULT_PROMPT`); `POST /api/settings` accepts `prompt_template`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`:

```python
def test_publish_stores_product_id(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "t")
    db.set_setting("printify_shop_id", "s")
    pf = tmp_path / "designs" / "7-print.png"
    pf.parent.mkdir(exist_ok=True)
    pf.write_bytes(b"png")
    did = insert("approved", file="designs/7-print.png", print_file="designs/7-print.png")
    monkeypatch.setattr(main.printify, "publish", lambda row: "prod-123")
    main.publish(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "published" and row["product_id"] == "prod-123"


def test_settings_roundtrips_prompt_template(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    out = main.get_settings()
    assert out["prompt_template"] == main.DEFAULT_PROMPT
    main.save_settings(main.SettingsBody(prompt_template="my prompt"))
    assert main.get_settings()["prompt_template"] == "my prompt"
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL — `product_id` is None; no `DEFAULT_PROMPT`.

- [ ] **Step 3: Implement** — in `main.py`:

Add the constant near the top:

```python
DEFAULT_PROMPT = (
    "Give me 20 t-shirt design ideas for [niche]. "
    "Format each as one line: phrase | style keywords. "
    "Example: reel cool dad | vintage, distressed, lake colors"
)
```

Add `prompt_template: str = ""` to `SettingsBody`. In `get_settings()` return it as text:

```python
@app.get("/api/settings")
def get_settings():
    keys = ("gemini_api_key", "printify_api_token", "printify_shop_id")
    out = {k: bool(db.get_setting(k)) for k in keys}
    out["prompt_template"] = db.get_setting("prompt_template") or DEFAULT_PROMPT
    return out
```

(`save_settings` already skips empty strings, so clearing the box just falls back to the default — acceptable.)

In `publish()`, change the success UPDATE to:

```python
        con.execute(
            "UPDATE designs SET status = 'published', error = NULL, product_id = ? WHERE id = ?",
            (str(product_id), design_id),
        )
```

- [ ] **Step 4: Run tests** — all pass. **Step 5: Commit** — `git add main.py tests/test_api.py && git commit -m "feat: persist printify product id, prompt_template setting"`

---

# Stage 3 — Dashboard stats

### Task 7: Snapshot strip

**Files:**
- Modify: `static/app.js`, `static/index.html`, `static/styles.css`

**Interfaces:**
- Consumes: globals `designs`, `render()` hook `renderDashboard()`.
- Produces: `#snapshot` markup; clicking a snapshot card sets the matching stage chip.

- [ ] **Step 1: Add markup** — in `index.html`, insert as the first child of `<section id="view-dashboard">`:

```html
      <div id="snapshot"></div>
      <div id="charts"><div id="chart_quality" class="panel"></div><div id="chart_styles" class="panel"></div></div>
```

- [ ] **Step 2: Add styles** — append to `styles.css`:

```css
#snapshot { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 18px; }
.stat-card {
  position: relative; background: linear-gradient(180deg, var(--slab), var(--slab-deep));
  border: 1px solid var(--mist); padding: 16px 18px 14px; cursor: pointer;
  text-align: left; transition: border-color 0.2s;
}
.stat-card:hover { border-color: var(--gold-pale); }
.stat-card .num { font-family: "Italiana", serif; font-size: 34px; color: var(--ivory); line-height: 1; }
.stat-card .lbl { font-size: 10px; font-weight: 500; letter-spacing: 0.24em; text-transform: uppercase; color: var(--stone); margin-top: 6px; }
.stat-card.alert .num { color: var(--gold-leaf); }
#charts { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 22px; }
@media (max-width: 860px) { #charts { grid-template-columns: 1fr; } }
.chart-title { font-size: 10px; font-weight: 500; letter-spacing: 0.32em; text-transform: uppercase; color: var(--gold-soft); margin-bottom: 12px; }
.chart-empty { color: var(--stone); font-size: 12.5px; padding: 12px 0; }
```

- [ ] **Step 3: Implement renderer** — in `app.js`, replace the `renderDashboard() {}` stub:

```js
function renderDashboard() {
  const counts = {};
  designs.forEach(d => {
    const t = d.status === "generating" ? "queued" : d.status;
    counts[t] = (counts[t] || 0) + 1;
  });
  const cards = [
    { stage: "pending", label: "Awaiting review", n: counts.pending || 0, alert: true },
    { stage: "queued", label: "In queue", n: counts.queued || 0 },
    { stage: "approved", label: "Ready to publish", n: counts.approved || 0 },
    { stage: "failed", label: "Failed", n: counts.failed || 0 },
  ];
  document.getElementById("snapshot").innerHTML = cards.map(c =>
    `<button class="stat-card ${c.alert && c.n ? "alert" : ""}" onclick="tab='${c.stage}';render()">` +
    `<div class="num">${c.n}</div><div class="lbl">${c.label}</div></button>`).join("");
  renderCharts();
}
function renderCharts() {} // filled in Task 8
```

- [ ] **Step 4: Verify in browser** — Dashboard shows four stat cards with live numbers; clicking "Failed" switches the stage chips + grid to Failed. No console errors.
- [ ] **Step 5: Commit** — `git add static/ && git commit -m "feat: dashboard snapshot strip"`

### Task 8: Quality-rate and style-breakdown charts

**Files:**
- Modify: `static/app.js`

**Interfaces:**
- Consumes: `tagsOf(d)`, `#chart_quality`, `#chart_styles`, `esc()`.
- Produces: `renderCharts()` full implementation.

- [ ] **Step 1: Implement** — replace the `renderCharts() {}` stub in `app.js`:

```js
function isoWeek(dateStr) {
  const d = new Date(dateStr + "Z");
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((t - yearStart) / 86400000 + 1) / 7);
  return `${t.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

function renderCharts() {
  // quality: approved (incl published) vs rejected per week, last 8 weeks with data
  const weeks = {};
  designs.forEach(d => {
    const ok = d.status === "approved" || d.status === "published";
    const bad = d.status === "rejected";
    if (!ok && !bad) return;
    const w = isoWeek(d.reviewed_at || d.created_at);
    weeks[w] = weeks[w] || { ok: 0, bad: 0 };
    weeks[w][ok ? "ok" : "bad"]++;
  });
  const keys = Object.keys(weeks).sort().slice(-8);
  const qEl = document.getElementById("chart_quality");
  qEl.innerHTML = `<div class="chart-title">Approval rate by week</div>` + (keys.length
    ? keys.map(w => {
        const { ok, bad } = weeks[w];
        const pct = Math.round(100 * ok / (ok + bad));
        return `<div style="display:flex;align-items:center;gap:10px;margin:7px 0;font:11px var(--mono);color:var(--stone)">` +
          `<span style="width:70px">${w.slice(5)}</span>` +
          `<svg width="100%" height="10" style="flex:1"><rect width="${pct}%" height="10" fill="var(--gold)"/>` +
          `<rect x="${pct}%" width="${100 - pct}%" height="10" fill="var(--mist)"/></svg>` +
          `<span style="width:78px;text-align:right">${pct}% of ${ok + bad}</span></div>`;
      }).join("")
    : `<div class="chart-empty">No reviewed designs yet — approve or reject a few and this fills in.</div>`);

  // styles: top 8 tags by count, with approval share
  const tags = {};
  designs.forEach(d => tagsOf(d).forEach(t => {
    tags[t] = tags[t] || { n: 0, ok: 0, judged: 0 };
    tags[t].n++;
    if (["approved", "published", "rejected"].includes(d.status)) {
      tags[t].judged++;
      if (d.status !== "rejected") tags[t].ok++;
    }
  }));
  const top = Object.entries(tags).sort((a, b) => b[1].n - a[1].n).slice(0, 8);
  const max = top.length ? top[0][1].n : 1;
  const sEl = document.getElementById("chart_styles");
  sEl.innerHTML = `<div class="chart-title">Top styles</div>` + (top.length
    ? top.map(([t, v]) =>
        `<div style="display:flex;align-items:center;gap:10px;margin:7px 0;font:11px var(--mono);color:var(--stone)">` +
        `<span style="width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(t)}">${esc(t)}</span>` +
        `<svg width="100%" height="10" style="flex:1"><rect width="${Math.round(100 * v.n / max)}%" height="10" fill="var(--gold-soft)"/></svg>` +
        `<span style="width:110px;text-align:right">${v.n}${v.judged ? ` · ${Math.round(100 * v.ok / v.judged)}% kept` : ""}</span></div>`)
      .join("")
    : `<div class="chart-empty">Tags appear once you have designs.</div>`);
}
```

- [ ] **Step 2: Verify in browser** — both chart panels render; weeks show gold/grey split bars with %; styles show ranked tag bars with counts. With an empty DB both show friendly empty text. No console errors.
- [ ] **Step 3: Commit** — `git add static/app.js && git commit -m "feat: quality and style charts"`

---

# Stage 4 — Add tab

### Task 9: Saved AI prompt box with copy button

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/styles.css`

**Interfaces:**
- Consumes: `GET/POST /api/settings` `prompt_template` (Task 6).
- Produces: `#prompt_box`, `copyPrompt()`, `flash(msg)` toast helper (reused by Stage 6 undo toast).

- [ ] **Step 1: Markup** — in `index.html`, insert as the first child of `<section id="view-add">`:

```html
      <section class="panel">
        <div class="panel-label">Idea prompt — copy into ChatGPT or Claude, paste the results below</div>
        <textarea id="prompt_box" spellcheck="false"></textarea>
        <div class="row">
          <button class="gilt" onclick="copyPrompt()">Copy prompt</button>
          <span class="hint">Edits save automatically</span>
        </div>
      </section>
```

- [ ] **Step 2: Styles** — append to `styles.css`:

```css
#toast {
  position: fixed; bottom: 26px; left: 50%; transform: translateX(-50%);
  background: var(--slab); border: 1px solid var(--gold-pale); color: var(--ivory);
  padding: 10px 20px; font-size: 13px; z-index: 50;
  display: flex; gap: 14px; align-items: center;
  box-shadow: 0 16px 40px rgba(0,0,0,0.6);
}
#toast[hidden] { display: none; }
#toast button { padding: 5px 12px; font-size: 10.5px; }
```

and in `index.html` add before `</body>`: `<div id="toast" hidden></div>`

- [ ] **Step 3: Implement** — append to `app.js`:

```js
let toastTimer;
function flash(msg, actionLabel, action) {
  const t = document.getElementById("toast");
  t.innerHTML = esc(msg) + (actionLabel ? ` <button onclick="toastAction()">${esc(actionLabel)}</button>` : "");
  window.toastAction = action || null;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
}

let promptSaveTimer, promptLoaded = false;
async function loadPrompt() {
  try {
    const s = await api("/api/settings");
    document.getElementById("prompt_box").value = s.prompt_template;
    promptLoaded = true;
  } catch (e) {}
}
document.getElementById("prompt_box").addEventListener("input", () => {
  if (!promptLoaded) return;
  clearTimeout(promptSaveTimer);
  promptSaveTimer = setTimeout(async () => {
    try {
      await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({prompt_template: document.getElementById("prompt_box").value})});
    } catch (e) { flash("Couldn't save the prompt — " + e.message); }
  }, 600);
});
async function copyPrompt() {
  const el = document.getElementById("prompt_box");
  try { await navigator.clipboard.writeText(el.value); flash("Prompt copied"); }
  catch (e) { el.focus(); el.select(); flash("Press ⌘C to copy"); }
}
loadPrompt();
```

- [ ] **Step 4: Verify in browser** — Add tab shows the default prompt; edit it, reload page: edit persisted. Copy button → toast "Prompt copied", clipboard holds text.
- [ ] **Step 5: Commit** — `git add static/ && git commit -m "feat: saved AI prompt box with copy button"`

### Task 10: CSV upload + duplicate warning (both input paths)

**Files:**
- Modify: `static/index.html`, `static/app.js`

**Interfaces:**
- Consumes: existing `generate()`, `/api/generate`, global `designs`, `flash()`.
- Produces: `parseCSV(text)`, `parseLines(text)`, `findDuplicates(items)`, `queueItems(items)` — `generate()` is rewritten on top of them.

- [ ] **Step 1: Markup** — in `index.html`, inside the composer panel of `#view-add`, add below the existing `.row`:

```html
        <div class="row">
          <label for="csv_file" style="min-width:0">Or upload a CSV</label>
          <input type="file" id="csv_file" accept=".csv,text/csv">
          <span class="hint" id="csv_state">two columns: phrase, style</span>
        </div>
```

- [ ] **Step 2: Implement** — append to `app.js`:

```js
// minimal CSV: two columns, handles quoted cells with commas/newlines
function parseCSV(text) {
  const rows = [];
  let cell = "", row = [], q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') q = false;
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      if (row.some(x => x.trim())) rows.push(row);
      row = [];
    } else cell += c;
  }
  row.push(cell);
  if (row.some(x => x.trim())) rows.push(row);
  if (rows.length && rows[0][0].trim().toLowerCase() === "phrase") rows.shift();
  return rows
    .map(r => [(r[0] || "").trim(), (r[1] || "").trim()])
    .filter(([p]) => p);
}

function parseLines(text) {
  return text.split("\n")
    .map(l => l.split("|").map(s => s.trim()))
    .map(([p, f]) => [p || "", f || ""])
    .filter(([p]) => p);
}

function findDuplicates(items) {
  const known = new Set(designs.map(d => d.phrase.trim().toLowerCase()));
  return items.filter(([p]) => known.has(p.trim().toLowerCase()));
}

async function queueItems(items) {
  const dups = findDuplicates(items);
  if (dups.length) {
    const list = dups.slice(0, 5).map(([p]) => `• ${p}`).join("\n");
    const skip = confirm(
      `${dups.length} of these look like designs you already have:\n${list}` +
      (dups.length > 5 ? "\n…" : "") +
      `\n\nOK = skip the duplicates, Cancel = queue everything anyway`);
    if (skip) {
      const dupSet = new Set(dups.map(([p]) => p.trim().toLowerCase()));
      items = items.filter(([p]) => !dupSet.has(p.trim().toLowerCase()));
    }
  }
  if (!items.length) { flash("Nothing new to queue"); return; }
  const text = items.map(([p, f]) => f ? `${p} | ${f}` : p).join("\n");
  await api("/api/generate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text})});
  flash(`Queued ${items.length} idea${items.length === 1 ? "" : "s"} (2 variations each)`);
  refresh();
}
```

Replace the existing `generate()` with:

```js
async function generate() {
  const items = parseLines(document.getElementById("input").value);
  if (!items.length) return;
  try {
    await queueItems(items);
    document.getElementById("input").value = "";
  } catch (e) { alert(e.message); }
}
```

And wire the file input (append to `app.js`):

```js
document.getElementById("csv_file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const state = document.getElementById("csv_state");
  try {
    const items = parseCSV(await file.text());
    if (!items.length) { state.textContent = "No ideas found in that file"; return; }
    state.textContent = `${items.length} ideas found`;
    await queueItems(items);
  } catch (e) { state.textContent = "Couldn't read that file: " + e.message; }
  ev.target.value = "";
});
```

- [ ] **Step 3: Verify in browser** —
- Paste two lines, one matching an existing phrase → confirm dialog lists the duplicate; OK queues only the new one (toast says so).
- Upload a CSV with header `phrase,style`, 3 rows, one quoted cell containing a comma → "3 ideas found", queued correctly (check In-queue stage).
- Empty/garbage file → friendly message, nothing queued.
- [ ] **Step 4: Commit** — `git add static/ && git commit -m "feat: CSV upload and duplicate warnings"`

---

# Stage 5 — Library

### Task 11: Search, filters, sort, ratings

**Files:**
- Modify: `static/index.html`, `static/styles.css`, `static/app.js`

**Interfaces:**
- Consumes: `tagsOf(d)`, `PATCH /api/designs/{id}` (Task 4), `esc()`.
- Produces: `renderLibrary()` full implementation; `libState` filter object; `setRating(id, n)`; `libCard(d)`; `openLightbox(id)` stub called on image click (filled by Task 12).

- [ ] **Step 1: Markup** — replace the contents of `<section id="view-library">` in `index.html` with:

```html
      <div class="workhead"><h2>Library</h2><span id="lib_count"></span></div>
      <section class="panel" id="lib_controls">
        <div class="row" style="margin-top:0">
          <input type="text" id="lib_q" placeholder="search phrases and styles…" style="flex:1;min-width:200px">
          <select id="lib_sort">
            <option value="new">Newest first</option>
            <option value="old">Oldest first</option>
            <option value="rating">Highest rated</option>
            <option value="az">A to Z</option>
          </select>
        </div>
        <div class="row"><span class="hint" style="min-width:60px">Status</span><span id="lib_status"></span></div>
        <div class="row"><span class="hint" style="min-width:60px">Tags</span><span id="lib_tags"></span></div>
        <div class="row">
          <span class="hint" style="min-width:60px">Rating</span>
          <select id="lib_minrating">
            <option value="0">Any</option><option value="1">★+</option><option value="2">★★+</option>
            <option value="3">★★★+</option><option value="4">★★★★+</option><option value="5">★★★★★</option>
          </select>
          <span class="hint">From</span><input type="date" id="lib_from" style="width:150px">
          <span class="hint">To</span><input type="date" id="lib_to" style="width:150px">
          <button onclick="libReset()">Clear filters</button>
        </div>
      </section>
      <div id="lib_grid"></div>
```

- [ ] **Step 2: Styles** — append to `styles.css`:

```css
select, input[type=date] {
  background: rgba(12, 10, 8, 0.6); color: var(--ivory);
  border: 1px solid var(--mist); border-radius: 0; padding: 9px 12px; font: 13px var(--mono);
}
.chip {
  display: inline-block; margin: 0 6px 6px 0; padding: 4px 12px; cursor: pointer;
  border: 1px solid var(--mist); background: none; color: var(--stone);
  font: 11px var(--mono); border-radius: 999px; transition: border-color 0.2s, color 0.2s;
}
.chip:hover { border-color: var(--gold-pale); color: var(--ivory); }
.chip.on { border-color: var(--gold); color: var(--gold-leaf); }
.stars { cursor: pointer; color: var(--vein-grey); font-size: 15px; letter-spacing: 2px; user-select: none; }
.stars .lit { color: var(--gold-soft); }
.status-chip { font: 10px var(--mono); color: var(--stone); border: 1px solid var(--mist); padding: 2px 8px; border-radius: 999px; }
```

- [ ] **Step 3: Implement** — in `app.js`, replace the Stage-1 `renderLibrary()` with:

```js
const libState = { q: "", statuses: new Set(), tags: new Set(), minRating: 0, from: "", to: "", sort: "new" };
function libReset() {
  Object.assign(libState, { q: "", minRating: 0, from: "", to: "", sort: "new" });
  libState.statuses.clear(); libState.tags.clear();
  document.getElementById("lib_q").value = "";
  document.getElementById("lib_sort").value = "new";
  document.getElementById("lib_minrating").value = "0";
  document.getElementById("lib_from").value = "";
  document.getElementById("lib_to").value = "";
  renderLibrary();
}
["lib_q", "lib_sort", "lib_minrating", "lib_from", "lib_to"].forEach(id =>
  document.getElementById(id).addEventListener("input", () => {
    libState.q = document.getElementById("lib_q").value.trim().toLowerCase();
    libState.sort = document.getElementById("lib_sort").value;
    libState.minRating = +document.getElementById("lib_minrating").value;
    libState.from = document.getElementById("lib_from").value;
    libState.to = document.getElementById("lib_to").value;
    renderLibrary();
  }));
function toggleSet(set, v) { set.has(v) ? set.delete(v) : set.add(v); renderLibrary(); }

function libFiltered() {
  let out = designs.filter(d => {
    if (libState.q && !(d.phrase + " " + d.filters).toLowerCase().includes(libState.q)) return false;
    const st = d.status === "generating" ? "queued" : d.status;
    if (libState.statuses.size && !libState.statuses.has(st)) return false;
    if (libState.tags.size) {
      const t = tagsOf(d);
      for (const need of libState.tags) if (!t.includes(need)) return false;
    }
    if ((d.rating || 0) < libState.minRating) return false;
    const day = (d.created_at || "").slice(0, 10);
    if (libState.from && day < libState.from) return false;
    if (libState.to && day > libState.to) return false;
    return true;
  });
  const by = {
    new: (a, b) => b.id - a.id,
    old: (a, b) => a.id - b.id,
    rating: (a, b) => (b.rating || 0) - (a.rating || 0) || b.id - a.id,
    az: (a, b) => a.phrase.localeCompare(b.phrase),
  }[libState.sort];
  return out.sort(by);
}

function stars(d) {
  let s = "";
  for (let i = 1; i <= 5; i++)
    s += `<span class="${i <= (d.rating || 0) ? "lit" : ""}" onclick="setRating(${d.id},${i})">★</span>`;
  return `<span class="stars" title="click to rate">${s}</span>`;
}
async function setRating(id, n) {
  const d = designs.find(x => x.id === id);
  const rating = d && d.rating === n ? 0 : n; // click current star again to clear
  try {
    await api(`/api/designs/${id}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({rating})});
    if (d) d.rating = rating;
    renderLibrary();
  } catch (e) { alert(e.message); }
}

function libCard(d) {
  const st = d.status === "generating" ? "queued" : d.status;
  const img = d.file
    ? `<img src="/${d.file}" loading="lazy" alt="${esc(d.phrase)}" onclick="openLightbox(${d.id})" style="cursor:zoom-in">`
    : `<div class="placeholder">no image</div>`;
  return `<div class="card"><div class="frame">${img}</div><div class="body">` +
    `<div class="phrase">${esc(d.phrase)}</div>` +
    `<div class="filters">${tagsOf(d).map(t => `<span class="chip" onclick="toggleSet(libState.tags,'${esc(t)}')">${esc(t)}</span>`).join("")}</div>` +
    `</div><div class="actions" style="justify-content:space-between">` +
    `${stars(d)}<span class="status-chip">${st}</span></div></div>`;
}

function renderLibrary() {
  if (currentView() !== "library") return;
  const stages = ["pending", "queued", "approved", "published", "failed", "rejected"];
  document.getElementById("lib_status").innerHTML = stages.map(s =>
    `<button class="chip ${libState.statuses.has(s) ? "on" : ""}" onclick="toggleSet(libState.statuses,'${s}')">${s}</button>`).join("");
  const allTags = [...new Set(designs.flatMap(tagsOf))].sort();
  document.getElementById("lib_tags").innerHTML = allTags.slice(0, 30).map(t =>
    `<button class="chip ${libState.tags.has(t) ? "on" : ""}" onclick="toggleSet(libState.tags,'${esc(t)}')">${esc(t)}</button>`).join("") ||
    `<span class="hint">tags appear as you make designs</span>`;
  const rows = libFiltered();
  document.getElementById("lib_count").textContent = rows.length === 1 ? "1 design" : `${rows.length} designs`;
  document.getElementById("lib_grid").innerHTML = rows.map(libCard).join("") ||
    `<div class="empty"><span class="fleuron">❦</span>No designs match — clear a filter or two.</div>`;
}
function openLightbox(id) {} // filled in Task 12
```

Note: `renderLibrary` early-returns unless the Library view is active, so polling doesn't fight with typing; the input listeners re-render on change. Also remove the Stage-1 note `#grid, #lib_grid` if not already present — `#lib_grid` must share `#grid`'s grid rule.

- [ ] **Step 4: Verify in browser** — text search narrows live; status/tag chips toggle gold; tag AND-logic (two tags = only designs with both); min-rating + date range work; sort switches order; stars click to set/clear rating and survive reload (PATCH persisted); clear filters resets everything. Typing in the search box is not interrupted by the 3s poll.
- [ ] **Step 5: Commit** — `git add static/ && git commit -m "feat: library search, filters, ratings"`

### Task 12: Lightbox with tags editor, downloads, Printify link

**Files:**
- Modify: `static/index.html`, `static/styles.css`, `static/app.js`

**Interfaces:**
- Consumes: `PATCH` endpoint, `DELETE`, `unreview`, `act()`, `libFiltered()`.
- Produces: `openLightbox(id)`, `closeLightbox()`; ←/→ keys navigate the current filtered result set.

- [ ] **Step 1: Markup** — add before `</body>` in `index.html`:

```html
<div id="lightbox" hidden onclick="if(event.target===this)closeLightbox()">
  <div id="lightbox_inner"></div>
</div>
```

- [ ] **Step 2: Styles** — append to `styles.css`:

```css
#lightbox {
  position: fixed; inset: 0; z-index: 40; background: rgba(10, 8, 6, 0.86);
  display: flex; align-items: center; justify-content: center; padding: 24px;
}
#lightbox[hidden] { display: none; }
#lightbox_inner {
  background: linear-gradient(180deg, var(--slab), var(--slab-deep));
  border: 1px solid var(--gold-pale); max-width: 860px; width: 100%; max-height: 92vh;
  overflow: auto; padding: 22px; display: grid; grid-template-columns: minmax(0, 1fr) 280px; gap: 20px;
}
#lightbox_inner img { width: 100%; border: 1px solid var(--mist); }
#lightbox_inner h3 { font-family: "Italiana", serif; font-size: 24px; font-weight: 400; margin: 0 0 8px; letter-spacing: 0.04em; }
.lb-row { margin: 10px 0; font-size: 12.5px; color: var(--stone); }
.lb-row input { width: 100%; }
@media (max-width: 700px) { #lightbox_inner { grid-template-columns: 1fr; } }
```

- [ ] **Step 3: Implement** — in `app.js`, replace the `openLightbox(id) {}` stub:

```js
let lbId = null;
function openLightbox(id) { lbId = id; renderLightbox(); }
function closeLightbox() { lbId = null; document.getElementById("lightbox").hidden = true; }
function lbMove(dir) {
  const rows = libFiltered().filter(d => d.file);
  const i = rows.findIndex(d => d.id === lbId);
  const next = rows[i + dir];
  if (next) { lbId = next.id; renderLightbox(); }
}
async function saveTags(id) {
  try {
    await api(`/api/designs/${id}`, {method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({tags: document.getElementById("lb_tags").value})});
    const d = designs.find(x => x.id === id);
    if (d) d.tags = document.getElementById("lb_tags").value;
    flash("Tags saved");
    renderLibrary();
  } catch (e) { alert(e.message); }
}
function renderLightbox() {
  const d = designs.find(x => x.id === lbId);
  if (!d) { closeLightbox(); return; }
  const st = d.status === "generating" ? "queued" : d.status;
  const actions = {
    pending: `<button class="gilt" onclick="act(this,${d.id},'approve');closeLightbox()">✓ Approve</button>
              <button onclick="act(this,${d.id},'reject');closeLightbox()">✕ Reject</button>`,
    approved: `<button onclick="act(this,${d.id},'unreview');closeLightbox()">↩ Back to review</button>`,
    rejected: `<button onclick="act(this,${d.id},'unreview');closeLightbox()">↩ Back to review</button>`,
    failed: `<button onclick="act(this,${d.id},'retry');closeLightbox()">↻ Retry</button>`,
  }[st] || "";
  document.getElementById("lightbox_inner").innerHTML =
    `<div>${d.file ? `<img src="/${d.file}" alt="${esc(d.phrase)}">` : `<div class="placeholder">no image</div>`}</div>` +
    `<div><h3>${esc(d.phrase)}</h3>` +
    `<div class="lb-row"><span class="status-chip">${st}</span> · ${(d.created_at || "").slice(0, 10)}</div>` +
    `<div class="lb-row">${stars(d)}</div>` +
    `<div class="lb-row">Style: ${esc(d.filters) || "—"}</div>` +
    `<div class="lb-row">Your tags<br><input type="text" id="lb_tags" value="${esc(d.tags || "")}" placeholder="comma, separated"> ` +
    `<button style="margin-top:6px" onclick="saveTags(${d.id})">Save tags</button></div>` +
    (d.error ? `<div class="lb-row" style="color:var(--clay)">${esc(d.error)}</div>` : "") +
    `<div class="lb-row">` +
    (d.print_file ? `<a href="/${d.print_file}" download><button>Download print file</button></a> ` : "") +
    (d.product_id ? `<a href="https://printify.com/app/products/${encodeURIComponent(d.product_id)}" target="_blank" rel="noopener"><button>Open in Printify</button></a>` : "") +
    `</div><div class="lb-row">${actions}</div></div>`;
  document.getElementById("lightbox").hidden = false;
}
document.addEventListener("keydown", (ev) => {
  if (lbId === null) return;
  if (ev.key === "Escape") closeLightbox();
  if (ev.key === "ArrowRight") lbMove(1);
  if (ev.key === "ArrowLeft") lbMove(-1);
});
```

- [ ] **Step 4: Verify in browser** — click a Library image: overlay opens with big image, stars, tags input; save tags → chip appears on cards and in filters; ←/→ steps through the filtered set; Esc and clicking the dark area close it; print-file download works for an upscaled design; a published design with product_id shows the Printify button.
- [ ] **Step 5: Commit** — `git add static/ && git commit -m "feat: library lightbox with tags, downloads, printify link"`

---

# Stage 6 — Review workflow

### Task 13: Cancel queued, delete rejected/failed, tab-title badge

**Files:**
- Modify: `static/app.js`

**Interfaces:**
- Consumes: `DELETE /api/designs/{id}` (Task 5), `card()`, `refresh()`.
- Produces: `removeDesign(btn, id, verb)`; badge in `document.title` and `#badge_pending`.

- [ ] **Step 1: Implement** — in `app.js`:

Add:

```js
async function removeDesign(btn, id, verb) {
  if (verb === "delete" && !confirm("Delete this design and its image files permanently?")) return;
  btn.disabled = true;
  busy++;
  try { await api(`/api/designs/${id}`, {method: "DELETE"}); }
  catch (e) { alert(e.message); }
  finally { busy--; }
  refresh();
}
```

In `card()`'s `buttons` map, add/extend entries:

```js
    queued: `<button onclick="removeDesign(this,${d.id},'cancel')">✕ Cancel</button>`,
    failed: `<button onclick="act(this,${d.id},'retry')">↻ Retry</button><button onclick="removeDesign(this,${d.id},'delete')">🗑 Delete</button>`,
    rejected: `<button onclick="act(this,${d.id},'retry')">↻ Re-queue</button><button onclick="act(this,${d.id},'unreview')">↩ Back to review</button><button onclick="removeDesign(this,${d.id},'delete')">🗑 Delete</button>`,
```

(Queued cards only render in the "In press" stage; `generating` rows must NOT get a cancel button — in `card()` guard with `d.status === "queued"`.)

In `refresh()`, after `render()`, add:

```js
    const pending = designs.filter(d => d.status === "pending").length;
    document.title = (pending ? `(${pending}) ` : "") + "The Atelier — T-Shirt Design House";
    document.getElementById("badge_pending").textContent = pending || "";
```

- [ ] **Step 2: Verify in browser** — queue an idea, cancel it from In-press (row disappears, no image generated); reject one then delete permanently (confirm dialog; file gone from `designs/` folder); browser tab title shows "(N)" when designs await review, plain when zero; sidebar Dashboard link shows the same count.
- [ ] **Step 3: Commit** — `git add static/app.js && git commit -m "feat: cancel queued, delete designs, pending badge"`

### Task 14: Bulk select + bulk approve/reject

**Files:**
- Modify: `static/app.js`, `static/styles.css`

**Interfaces:**
- Consumes: `act()` internals (`/api/designs/{id}/approve|reject`), `render()`, `card()`.
- Produces: `selected` Set; `bulkAct(action)`; `#bulkbar` element rendered inside the Dashboard when on the pending stage.

- [ ] **Step 1: Styles** — append to `styles.css`:

```css
#bulkbar { display: flex; gap: 10px; align-items: center; margin: 0 0 14px; }
.card .pick { position: absolute; top: 16px; left: 16px; z-index: 3; width: 17px; height: 17px; accent-color: var(--gold); }
.card.selected { border-color: var(--gold); }
```

- [ ] **Step 2: Implement** — in `app.js`:

```js
const selected = new Set();
function togglePick(id, on) {
  on ? selected.add(id) : selected.delete(id);
  render();
}
function selectAllPending(on) {
  selected.clear();
  if (on) designs.filter(d => d.status === "pending").forEach(d => selected.add(d.id));
  render();
}
async function bulkAct(action) {
  const ids = [...selected];
  selected.clear();
  busy++;
  try { await Promise.all(ids.map(id => api(`/api/designs/${id}/${action}`, {method: "POST"}))); }
  catch (e) { alert(e.message); }
  finally { busy--; }
  flash(`${ids.length} design${ids.length === 1 ? "" : "s"} ${action === "approve" ? "approved" : "rejected"}`);
  refresh();
}
```

In `card()`, for pending designs prepend a checkbox to the frame (inside the returned HTML, right after `<div class="frame">`):

```js
  const pick = d.status === "pending"
    ? `<input type="checkbox" class="pick" ${selected.has(d.id) ? "checked" : ""} onclick="togglePick(${d.id}, this.checked)">`
    : "";
```

and add `${pick}` right after `<div class="frame">`, plus `selected.has(d.id) ? " selected" : ""` on the card's class.

In `render()`, when `tab === "pending"`, prepend a bulk bar above the grid (insert into `#tabs`' sibling — simplest: give the grid HTML a leading block):

```js
  const bulkbar = tab === "pending" && shown.length
    ? `<div id="bulkbar" style="grid-column:1/-1">
         <label class="hint"><input type="checkbox" onclick="selectAllPending(this.checked)" ${selected.size && selected.size === shown.length ? "checked" : ""}> Select all</label>
         ${selected.size ? `<button class="gilt" onclick="bulkAct('approve')">✓ Approve ${selected.size}</button>
         <button onclick="bulkAct('reject')">✕ Reject ${selected.size}</button>` : `<span class="hint">tick designs to act on several at once</span>`}
       </div>`
    : "";
  document.getElementById("grid").innerHTML = bulkbar + (shown.map(card).join("") || `...existing empty state...`);
```

Also clear stale selections in `refresh()` after fetching: `[...selected].forEach(id => { const d = designs.find(x => x.id === id); if (!d || d.status !== "pending") selected.delete(id); });`

- [ ] **Step 2b: Verify in browser** — tick 2 of 3 pending cards → "Approve 2 / Reject 2" buttons appear; approve moves both to Approved with one toast; select-all works; selection survives the 3s poll; boxes only on pending cards.
- [ ] **Step 3: Commit** — `git add static/ && git commit -m "feat: bulk approve/reject"`

### Task 15: Variation grouping in the review stage

**Files:**
- Modify: `static/app.js`, `static/styles.css`

**Interfaces:**
- Consumes: `card()`, `render()`.
- Produces: pending stage groups cards by `phrase + "|" + filters` under a shared header.

- [ ] **Step 1: Styles** — append to `styles.css`:

```css
.vargroup { grid-column: 1 / -1; }
.vargroup .vg-head { font-family: "Italiana", serif; font-size: 17px; letter-spacing: 0.06em; color: var(--ivory); margin: 8px 0 10px; }
.vargroup .vg-head .vg-style { font: 11px var(--mono); color: var(--stone); margin-left: 10px; }
.vargroup .vg-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 18px; }
```

- [ ] **Step 2: Implement** — in `render()`, replace the plain `shown.map(card).join("")` for the pending tab with grouped output:

```js
  let cardsHtml;
  if (tab === "pending") {
    const groups = new Map();
    shown.forEach(d => {
      const k = d.phrase + "|" + d.filters;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(d);
    });
    cardsHtml = [...groups.values()].map(g =>
      g.length > 1
        ? `<div class="vargroup"><div class="vg-head">${esc(g[0].phrase)}<span class="vg-style">${esc(g[0].filters)}</span></div>` +
          `<div class="vg-cards">${g.map(card).join("")}</div></div>`
        : card(g[0], 0)
    ).join("");
  } else {
    cardsHtml = shown.map(card).join("");
  }
```

then use `cardsHtml` where `shown.map(card).join("")` was used.

- [ ] **Step 3: Verify in browser** — with 2 variations of the same idea pending, they appear side by side under one phrase header; singletons render as normal cards; other stages unchanged.
- [ ] **Step 4: Commit** — `git add static/ && git commit -m "feat: variation grouping in review"`

### Task 16: Keyboard review mode + undo toast

**Files:**
- Modify: `static/app.js`, `static/styles.css`

**Interfaces:**
- Consumes: `act()`, `unreview` endpoint, `openLightbox()`, `flash()`.
- Produces: keyboard handler active on Dashboard pending stage: ←/→ or j/k move focus, A approve, R reject, U undo, Space lightbox; `lastAction {id, action}` for undo; visible key legend.

- [ ] **Step 1: Styles** — append to `styles.css`:

```css
.card.kbfocus { outline: 2px solid var(--gold); outline-offset: 3px; }
#keylegend { font: 10.5px var(--mono); color: var(--stone); margin: 0 0 12px; }
#keylegend b { color: var(--gold-soft); font-weight: 500; }
```

- [ ] **Step 2: Implement** — append to `app.js`:

```js
let kbIndex = -1, lastAction = null;
function pendingIds() { return designs.filter(d => d.status === "pending").map(d => d.id); }
function kbHighlight() {
  document.querySelectorAll(".card.kbfocus").forEach(c => c.classList.remove("kbfocus"));
  const ids = pendingIds();
  if (kbIndex < 0 || kbIndex >= ids.length) return;
  const el = document.querySelector(`.card[data-id="${ids[kbIndex]}"]`);
  if (el) { el.classList.add("kbfocus"); el.scrollIntoView({block: "nearest", behavior: "smooth"}); }
}
async function kbAct(action) {
  const ids = pendingIds();
  if (kbIndex < 0 || kbIndex >= ids.length) return;
  const id = ids[kbIndex];
  lastAction = { id, action };
  busy++;
  try { await api(`/api/designs/${id}/${action}`, {method: "POST"}); }
  catch (e) { alert(e.message); }
  finally { busy--; }
  flash(`${action === "approve" ? "Approved" : "Rejected"} — press U to undo`, "Undo", undoLast);
  await refresh();
  kbIndex = Math.min(kbIndex, pendingIds().length - 1);
  kbHighlight();
}
async function undoLast() {
  if (!lastAction) return;
  try { await api(`/api/designs/${lastAction.id}/unreview`, {method: "POST"}); flash("Moved back to review"); }
  catch (e) { alert(e.message); }
  lastAction = null;
  refresh();
}
document.addEventListener("keydown", (ev) => {
  if (lbId !== null) return; // lightbox has its own keys
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if (currentView() !== "dashboard" || tab !== "pending") return;
  const ids = pendingIds();
  if (!ids.length) return;
  const k = ev.key.toLowerCase();
  if (k === "arrowright" || k === "j") { kbIndex = Math.min(kbIndex + 1, ids.length - 1); kbHighlight(); }
  else if (k === "arrowleft" || k === "k") { kbIndex = Math.max(kbIndex - 1, 0); kbHighlight(); }
  else if (k === "a") kbAct("approve");
  else if (k === "r") kbAct("reject");
  else if (k === "u") undoLast();
  else if (k === " " && kbIndex >= 0) { ev.preventDefault(); openLightbox(ids[kbIndex]); }
});
```

`card()` must expose the id on the element: add `data-id="${d.id}"` to the card's outer div. And in `render()`, when `tab === "pending"` and there are cards, prepend to the grid HTML:

```js
  const legend = `<div id="keylegend" style="grid-column:1/-1"><b>→/←</b> move · <b>A</b> approve · <b>R</b> reject · <b>U</b> undo · <b>space</b> zoom</div>`;
```

(concatenate `legend` into the pending-tab HTML before the cards). Also re-apply `kbHighlight()` at the end of `render()`.

- [ ] **Step 3: Verify in browser** — on To-review: → highlights first card with gold ring; A approves it and the ring lands on the next; U (or toast Undo) brings it back to pending; Space opens the lightbox; keys do nothing while typing in the search box, on other tabs, or in other stages.
- [ ] **Step 4: Commit** — `git add static/ && git commit -m "feat: keyboard review mode with undo"`

---

# Stage 7 — Settings & safety

### Task 17: Test-connection endpoints + buttons

**Files:**
- Modify: `main.py`, `static/index.html`, `static/app.js`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `POST /api/test/gemini`, `POST /api/test/printify` returning `{ok: bool, message: str}` (never raising for a bad key — the failure IS the payload).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`:

```python
class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or []
        self.text = text
    def json(self):
        return self._payload


def test_test_gemini_no_key(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    out = main.test_gemini()
    assert out == {"ok": False, "message": "No Gemini key saved yet"}


def test_test_gemini_ok(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "k")
    monkeypatch.setattr(main.requests, "get", lambda *a, **kw: FakeResp(200))
    assert main.test_gemini()["ok"] is True


def test_test_printify_wrong_shop(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "t")
    db.set_setting("printify_shop_id", "42")
    monkeypatch.setattr(main.requests, "get",
                        lambda *a, **kw: FakeResp(200, payload=[{"id": 7, "title": "Other"}]))
    out = main.test_printify()
    assert out["ok"] is False and "42" in out["message"]
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL, no `test_gemini`.

- [ ] **Step 3: Implement** — in `main.py`, add `import requests` at the top, then:

```python
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
```

- [ ] **Step 4: Run tests** — all pass.

- [ ] **Step 5: Wire the UI** — in `index.html` Settings panel, add a Test button + result span to the Gemini row and (after the Shop ID row) a Printify test row:

```html
          <button onclick="testConn('gemini')">Test</button>
          <span class="hint" id="test_gemini"></span>
```
(in the Gemini row, after Save) and

```html
        <div class="row">
          <button onclick="testConn('printify')">Test Printify</button>
          <span class="hint" id="test_printify"></span>
        </div>
```

In `app.js`:

```js
async function testConn(which) {
  const el = document.getElementById("test_" + which);
  el.textContent = "testing…";
  try {
    const out = await api("/api/test/" + which, {method: "POST"});
    el.textContent = (out.ok ? "✓ " : "✗ ") + out.message;
    el.style.color = out.ok ? "var(--gold-soft)" : "var(--clay)";
  } catch (e) { el.textContent = "✗ " + e.message; el.style.color = "var(--clay)"; }
}
```

- [ ] **Step 6: Verify in browser** — with no keys: both tests give plain-language "save a key first" messages; with a garbage key: provider error text appears in clay red.
- [ ] **Step 7: Commit** — `git add main.py static/ tests/test_api.py && git commit -m "feat: test-connection buttons for gemini and printify"`

### Task 18: CSV export

**Files:**
- Modify: `main.py`, `static/index.html`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `GET /api/export.csv` — header row `id,phrase,style,status,tags,rating,product_id,created_at`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_api.py`:

```python
def test_export_csv(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    insert("published", tags="funny", rating=4, product_id="p1")
    resp = main.export_csv()
    body = resp.body.decode()
    lines = body.strip().splitlines()
    assert lines[0] == "id,phrase,style,status,tags,rating,product_id,created_at"
    assert "dog dad" in lines[1] and "p1" in lines[1]
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL, no `export_csv`.

- [ ] **Step 3: Implement** — in `main.py`, add `import csv` and `import io` at top, `Response` to the fastapi.responses import, then:

```python
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
        headers={"Content-Disposition": 'attachment; filename="atelier-designs.csv"'},
    )
```

- [ ] **Step 4: Run tests** — all pass.
- [ ] **Step 5: Button** — in the Settings view of `index.html`, add a new panel after Connections:

```html
      <section class="panel">
        <div class="panel-label">Your data</div>
        <div class="row" style="margin-top:0">
          <a href="/api/export.csv" download><button>Export designs (CSV)</button></a>
          <span class="hint">spreadsheet of every design: phrase, style, status, tags, rating</span>
        </div>
      </section>
```

- [ ] **Step 6: Verify in browser** — click Export: CSV downloads, opens in Numbers/Excel with the right columns.
- [ ] **Step 7: Commit** — `git add main.py static/ tests/test_api.py && git commit -m "feat: csv export of all designs"`

### Task 19: One-click backup + generation info panel

**Files:**
- Modify: `main.py`, `static/index.html`, `static/app.js`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `GET /api/backup` returning a zip (`designs.db` + `designs/` images), filename `atelier-backup-YYYY-MM-DD.zip`.

- [ ] **Step 1: Write the failing test** — append to `tests/test_api.py`:

```python
import zipfile


def test_backup_zip_contains_db_and_images(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    img = tmp_path / "designs" / "1.png"
    img.parent.mkdir(exist_ok=True)
    img.write_bytes(b"png")
    resp = main.backup()
    with zipfile.ZipFile(resp.path) as z:
        names = z.namelist()
    assert "designs.db" in names and "designs/1.png" in names
```

- [ ] **Step 2: Run to verify failure** — Expected: FAIL, no `backup`.

- [ ] **Step 3: Implement** — in `main.py`, add imports `import datetime`, `import tempfile`, `import zipfile`, and `from starlette.background import BackgroundTask`, then:

```python
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
    fname = "atelier-backup-%s.zip" % datetime.date.today().isoformat()
    return FileResponse(path, filename=fname, media_type="application/zip",
                        background=BackgroundTask(os.remove, path))
```

- [ ] **Step 4: Run tests** — all pass. (The test reads `resp.path` before the background task runs — background tasks only execute when served over HTTP, so the temp file still exists in the direct call; that's fine.)

- [ ] **Step 5: UI** — add to the "Your data" panel in `index.html`:

```html
        <div class="row">
          <a href="/api/backup"><button>Back up everything</button></a>
          <span class="hint">one zip with your database and every image — keep a copy somewhere safe</span>
        </div>
```

and a read-only generation info panel below it:

```html
      <section class="panel">
        <div class="panel-label">Generation</div>
        <div class="hint" id="gen_info">loading…</div>
      </section>
```

with, in `app.js` `refresh()` after the title update:

```js
    document.getElementById("gen_info").textContent = status.local
      ? "Generating on your local GPU — no daily cap."
      : `Gemini free tier: ${status.today}/${status.cap} images used today · 2 variations per idea · ~2 images/min.`;
```

- [ ] **Step 6: Verify in browser** — Back up downloads a zip; unzip it: `designs.db` + `designs/` images inside. Generation panel shows live numbers.
- [ ] **Step 7: Commit** — `git add main.py static/ tests/test_api.py && git commit -m "feat: one-click backup and generation info"`

---

## Final verification (after all stages)

- [ ] `.venv/bin/python -m pytest tests/ -q` — everything green.
- [ ] Full browser pass: all four tabs, mobile width, keyboard mode, a full cycle (add → cancel one → review with keys → approve → tag/rate in library → export/backup).
- [ ] `git log --oneline` shows one commit per task.
