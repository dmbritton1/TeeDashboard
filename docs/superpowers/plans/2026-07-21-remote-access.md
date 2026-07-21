# Remote Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a few known people open the dashboard from any device via a shareable link, gated by a shared access code and a queue cap, with generation still running locally on this machine.

**Architecture:** The local FastAPI server is exposed through a Cloudflare Quick Tunnel (ops, no code). In the app, a single shared passphrase (stored in the existing `settings` table) gates the generation endpoints via a FastAPI dependency, and a total in-flight queue cap bounds abuse. Read-only endpoints stay open so the page can load and prompt for the code.

**Tech Stack:** FastAPI, SQLite (existing `db.py` helpers), vanilla JS frontend, `cloudflared` (external binary, not a Python dep).

## Global Constraints

- Gate is **off until a code is set**: `db.get_setting("access_code")` returning `None`/empty means generation endpoints are open (preserves current local-only workflow).
- Access code is checked via the `X-Access-Code` request header (FastAPI maps this to a `x_access_code` header param).
- Read-only endpoints (`/api/designs`, `/api/status`, `/`, `/designs/*`) are NEVER gated.
- `MAX_QUEUE = 30`, counting rows with `status IN ('queued','generating')`; lives in `worker.py` next to `DAILY_CAP`.
- Follow the existing test pattern: `monkeypatch.setattr(db, "DB_PATH", ...)` then `db.init()` (see `tests/test_worker.py`).
- No new Python dependencies. No accounts, no per-user identity, no stable-URL/custom-domain work, no GitHub Pages frontend.

---

### Task 1: Access-code gate on generation endpoints (backend)

**Files:**
- Modify: `main.py` (imports line 5; `SettingsBody` line 43-46; `/api/generate` line 49-61; `/api/test` line 64-75; `status()` handler line ~160)
- Test: `tests/test_api.py` (create)

**Interfaces:**
- Consumes: `db.get_setting`, `db.set_setting` (existing).
- Produces: `require_access_code(x_access_code: str | None) -> None` — a FastAPI dependency raising `HTTPException(401)` when a code is set and the header doesn't match; no-op otherwise. Applied to `/api/generate` and `/api/test`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api.py`. Setting `db.DB_PATH` before importing `main` makes `main`'s import-time `db.init()` use the temp DB and the worker thread idle (no key, no local GPU).

```python
import os
import tempfile

import db

# point the app at a throwaway DB before main's import-time db.init() runs
db.DB_PATH = os.path.join(tempfile.mkdtemp(), "api.db")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)


def _reset():
    with db.connect() as con:
        con.execute("DELETE FROM designs")
        con.execute("DELETE FROM settings")


def test_generation_open_when_no_code_set():
    _reset()
    r = client.post("/api/test", json={"text": "a red dragon"})
    assert r.status_code == 200, r.text


def test_generation_blocked_without_code_header_when_code_set():
    _reset()
    db.set_setting("access_code", "hunter2")
    r = client.post("/api/test", json={"text": "a red dragon"})
    assert r.status_code == 401


def test_generation_blocked_with_wrong_code():
    _reset()
    db.set_setting("access_code", "hunter2")
    r = client.post("/api/test", json={"text": "a red dragon"},
                    headers={"X-Access-Code": "nope"})
    assert r.status_code == 401


def test_generation_allowed_with_correct_code():
    _reset()
    db.set_setting("access_code", "hunter2")
    r = client.post("/api/test", json={"text": "a red dragon"},
                    headers={"X-Access-Code": "hunter2"})
    assert r.status_code == 200, r.text


def test_reading_designs_never_gated():
    _reset()
    db.set_setting("access_code", "hunter2")
    assert client.get("/api/designs").status_code == 200
    assert client.get("/api/status").status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api.py -q`
Expected: FAIL — `test_generation_blocked_*` return 200 (no gate exists yet).

- [ ] **Step 3: Add the dependency and apply it**

In `main.py`, change the FastAPI import (line 5) to add `Depends` and `Header`:

```python
from fastapi import Depends, FastAPI, Header, HTTPException
```

Add the dependency after `BASE`/app setup (e.g. just before `class GenerateBody`):

```python
def require_access_code(x_access_code: str | None = Header(default=None)) -> None:
    """Gate generation once a shared code is set; open when no code exists."""
    code = db.get_setting("access_code")
    if code and x_access_code != code:
        raise HTTPException(401, "Access code required")
```

Apply it to both generation endpoints by adding a dependency parameter:

```python
@app.post("/api/generate")
def generate(body: GenerateBody, _: None = Depends(require_access_code)):
```

```python
@app.post("/api/test")
def generate_test(body: TestBody, _: None = Depends(require_access_code)):
```

- [ ] **Step 4: Let the code be set and reported**

Add `access_code` to `SettingsBody` (line 43-46) so it can be saved:

```python
class SettingsBody(BaseModel):
    gemini_api_key: str = ""
    printify_api_token: str = ""
    printify_shop_id: str = ""
    access_code: str = ""
```

(The existing `save_settings` loop already persists any non-empty `SettingsBody` field, so `access_code` saves with no further change.)

Report whether a code is set on `/api/status` — this is the object the frontend's `refresh()` already reads (not `/api/settings`). In the `status()` handler, add one key to the returned dict:

```python
        "access_code": bool(db.get_setting("access_code")),
```

(Place it alongside the existing `"has_key"` / `"printify_ready"` entries.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api.py -q`
Expected: PASS (5 passed).

- [ ] **Step 6: Confirm the existing suite still passes**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all previous tests + the 5 new ones pass.

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_api.py
git commit -m "feat: shared access code gate on generation endpoints"
```

---

### Task 2: Queue cap on generation endpoints (backend)

**Files:**
- Modify: `worker.py` (add `MAX_QUEUE` next to `DAILY_CAP` line 9)
- Modify: `main.py` (`/api/generate`, `/api/test` — reject when queue is full)
- Test: `tests/test_api.py` (extend)

**Interfaces:**
- Consumes: `worker.MAX_QUEUE`, `db.connect` (existing).
- Produces: both generation endpoints return `429` when `COUNT(*) WHERE status IN ('queued','generating') >= worker.MAX_QUEUE`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:

```python
import worker  # noqa: E402


def test_queue_cap_rejects_when_full():
    _reset()
    with db.connect() as con:
        for _ in range(worker.MAX_QUEUE):
            con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('x','','queued')")
    r = client.post("/api/test", json={"text": "one too many"})
    assert r.status_code == 429


def test_queue_cap_allows_below_limit():
    _reset()
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status) VALUES ('x','','queued')")
    r = client.post("/api/test", json={"text": "still room"})
    assert r.status_code == 200, r.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api.py -k queue_cap -q`
Expected: FAIL — `test_queue_cap_rejects_when_full` returns 200 (no cap yet).

- [ ] **Step 3: Add the cap constant**

In `worker.py`, next to `DAILY_CAP` (line 9):

```python
DAILY_CAP = 450        # stop 50 short of the ~500/day free-tier cap
MAX_QUEUE = 30         # bound in-flight images so a shared link can't flood the GPU
SECONDS_BETWEEN = 31   # ~2 images/min free-tier pace
```

- [ ] **Step 4: Enforce it in both endpoints**

Add a helper in `main.py` (near the dependency):

```python
def _queue_full() -> bool:
    with db.connect() as con:
        n = con.execute(
            "SELECT COUNT(*) AS c FROM designs WHERE status IN ('queued', 'generating')"
        ).fetchone()["c"]
    return n >= worker.MAX_QUEUE
```

In `/api/generate`, immediately after `if not items: raise ...`:

```python
    if _queue_full():
        raise HTTPException(429, "Queue is full - try again shortly")
```

In `/api/test`, immediately after `if not text: raise ...`:

```python
    if _queue_full():
        raise HTTPException(429, "Queue is full - try again shortly")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api.py -q`
Expected: PASS (7 passed).

- [ ] **Step 6: Commit**

```bash
git add worker.py main.py tests/test_api.py
git commit -m "feat: cap in-flight queue to bound shared-link abuse"
```

---

### Task 3: Frontend access-code handling

**Files:**
- Modify: `static/index.html` (settings panel markup ~line 37-49; `api()` ~line 66-74; `saveSettings()`; `refresh()` status display)

**Interfaces:**
- Consumes: `/api/settings` (now reports `access_code: bool`), `X-Access-Code` header contract from Task 1, `429` from Task 2.
- Produces: browser stores the code in `localStorage` under key `accessCode` and sends it on every `api()` call; on `401` it prompts and retries; on `429` it surfaces the queue-full message.

This stack has no JS test harness, so verification is a scripted browser drive (Step 4), consistent with the rest of the untested frontend.

- [ ] **Step 1: Add the access-code field to the settings panel**

In `static/index.html`, inside the settings `<section class="panel">` (after the Printify row, before `</section>` at line 49):

```html
    <div class="row">
      <label>Access code</label>
      <input type="password" id="access_code" placeholder="set a code to gate the shared link">
      <button onclick="forgetCode()">Forget code on this device</button>
      <span class="hint" id="code_state"></span>
    </div>
```

- [ ] **Step 2: Send the code on every request and handle 401**

Replace the `api()` function (line 66-74) with:

```javascript
async function api(path, opts) {
  opts = opts || {};
  const code = localStorage.getItem("accessCode");
  if (code) opts.headers = Object.assign({}, opts.headers, {"X-Access-Code": code});
  let r = await fetch(path, opts);
  if (r.status === 401) {
    const entered = prompt("Enter the access code:");
    if (entered) {
      localStorage.setItem("accessCode", entered);
      opts.headers = Object.assign({}, opts.headers, {"X-Access-Code": entered});
      r = await fetch(path, opts);
    }
  }
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return r.json();
}
```

- [ ] **Step 3: Save the code, report its state, and allow forgetting it**

In `saveSettings()`, add `access_code` to the body and clear the input after:

```javascript
  const body = {
    gemini_api_key: document.getElementById("gemini_key").value,
    printify_api_token: document.getElementById("printify_token").value,
    printify_shop_id: document.getElementById("printify_shop").value,
    access_code: document.getElementById("access_code").value,
  };
```

After the existing two `.value = ""` clears in `saveSettings()`, add:

```javascript
  document.getElementById("access_code").value = "";
```

Add the `forgetCode` helper (next to `saveSettings`):

```javascript
function forgetCode() {
  localStorage.removeItem("accessCode");
  alert("Access code cleared on this device.");
}
```

In `refresh()`, where `key_state` is set (line ~148), also reflect the code state:

```javascript
    document.getElementById("code_state").textContent = status.access_code ? "code set ✓" : "no code — link is open";
```

- [ ] **Step 4: Verify in a browser (manual drive)**

1. Start the server: `.venv\Scripts\python.exe -m uvicorn main:app --port 8000`
2. Open `http://localhost:8000`, set an Access code (e.g. `hunter2`), Save. Status shows "code set ✓".
3. Open a private window (no stored code), go to the Test tab, submit a prompt → a code prompt appears. Enter `hunter2` → the image queues (card shows "generating…").
4. Click "Forget code on this device", submit again → prompted again. Enter wrong code → alert shows "Access code required".
Expected: all four behaviors as described.

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat: frontend access-code prompt, storage, and settings field"
```

---

### Task 4: Document sharing via Cloudflare tunnel

**Files:**
- Modify: `README.md` (add a "Share with a few people" section)

- [ ] **Step 1: Add the sharing section**

Append to `README.md`:

```markdown
## Share with a few people (any device)

The dashboard runs on this machine; a tunnel gives it a public link so others
can open it in a browser and queue images. Generation still happens locally.

1. Set an **Access code** in the dashboard settings (gates image generation; a
   leaked link alone can't queue work). Without a code the link is open.
2. Run the server bound to all interfaces:

       .venv\Scripts\uvicorn main:app --host 0.0.0.0 --port 8000

3. In another terminal, start a Cloudflare Quick Tunnel (install `cloudflared`
   first from Cloudflare's site):

       cloudflared tunnel --url http://localhost:8000

   It prints a `https://<random>.trycloudflare.com` URL. Share it. Anyone who
   opens it gets the dashboard and, on first generate, is asked for the access
   code.

Notes: the tunnel URL changes each time you restart `cloudflared`. Generation is
serialized on one GPU, so images queue (~a few minutes each); the queue is capped
at 30 in-flight to prevent flooding.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: how to share the dashboard via a Cloudflare tunnel"
```

---

## Notes for the implementer

- Run all Python commands from the repo root with the venv interpreter:
  `.venv\Scripts\python.exe -m pytest -q`.
- The worker thread starts on `import main`; in tests it idles because
  `has_local()` is false and no Gemini key is set, so it never writes.
- Do not gate `/api/designs` or `/api/status` — the page must load before the
  user can be asked for a code.
