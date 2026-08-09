# Login Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put a real login page in front of the whole dashboard so only the owner and one friend can reach it over the shared Cloudflare tunnel link.

**Architecture:** A single FastAPI HTTP middleware runs before routing and rejects any request without a valid `auth` cookie, which covers the API routes *and* the `/designs` and `/static` `StaticFiles` mounts. The password lives in `.env` as `DASHBOARD_PASSWORD`; the cookie's value is `sha256(password)`, so there is no session store, no secret key, and changing the password invalidates every cookie for free. The existing `X-Access-Code` header gate is deleted rather than kept alongside.

**Tech Stack:** FastAPI, Starlette, SQLite, vanilla JS. Python stdlib only — `hashlib`, `hmac`, `time`, `urllib.parse`. **No new dependencies.**

## Global Constraints

- No new Python packages. `python-multipart` is **not** installed, so `fastapi.Form` is unavailable — parse the login form body with `urllib.parse.parse_qs` from stdlib.
- Password env var name is exactly `DASHBOARD_PASSWORD`.
- Cookie name is exactly `auth`; value is exactly `hashlib.sha256(password.encode()).hexdigest()`.
- Cookie flags: `httponly=True`, `samesite="lax"`, `max_age=2592000` (30 days), and `secure` computed per-request from `x-forwarded-proto` falling back to the request scheme.
- All password/cookie comparisons use `hmac.compare_digest`, never `==`.
- Run tests with `.venv/bin/pytest -q` (this is the command in README.md).
- Comments in this codebase explain *why*, not *what*. Match that.

---

### Task 1: The gate — password, cookie, middleware, login page

**Files:**
- Modify: `main.py` (imports at 1-22, then insert after the `/` route at 43-45)
- Create: `static/login.html`
- Modify: `static/styles.css` (append at end)
- Create: `tests/conftest.py`
- Modify: `tests/test_access.py` (add new tests; the old ones keep passing this task)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `main.PASSWORD: str`, `main.COOKIE: str` (the sha256 hex), `main.OPEN_PATHS: set[str]`. Task 2's tests and Task 3's frontend rely on `POST /login`, `POST /logout`, and the `auth` cookie name.

- [ ] **Step 1: Add the test password fixture**

`main.py` will refuse to import without `DASHBOARD_PASSWORD`, and `tests/test_access.py` imports `main` at module level. pytest loads `conftest.py` before collecting test modules, so setting it here covers every test file.

Create `tests/conftest.py`:

```python
"""Set the dashboard password before any test module imports main.

main.py exits at import time when DASHBOARD_PASSWORD is unset, and its
load_dotenv() call does not override values already in the environment, so
this stays deterministic even on a machine with a real password in .env.
"""
import os

os.environ["DASHBOARD_PASSWORD"] = "test-password"
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_access.py`, directly below the existing `_reset` helper:

```python
def _anon():
    """A client with no cookie. Fresh each time: TestClient keeps cookies."""
    return TestClient(main.app)


def test_api_refused_without_cookie():
    assert _anon().get("/api/designs").status_code == 401


def test_dashboard_redirects_to_login_without_cookie():
    r = _anon().get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_design_images_refused_without_cookie():
    # the /designs mount was the real hole: a leaked link showed every image
    r = _anon().get("/designs/anything.png", follow_redirects=False)
    assert r.status_code == 303


def test_login_page_and_its_stylesheet_are_reachable():
    a = _anon()
    assert a.get("/login").status_code == 200
    assert a.get("/static/styles.css").status_code == 200


def test_correct_password_sets_cookie_and_opens_the_door():
    a = _anon()
    r = a.post("/login", data={"password": "test-password"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert a.cookies.get("auth")
    assert a.get("/api/designs").status_code == 200


def test_wrong_password_sets_no_cookie():
    a = _anon()
    r = a.post("/login", data={"password": "nope"}, follow_redirects=False)
    assert r.status_code == 200
    assert "Incorrect" in r.text
    assert not a.cookies.get("auth")


def test_logout_clears_the_cookie():
    a = _anon()
    a.post("/login", data={"password": "test-password"})
    a.post("/logout", follow_redirects=False)
    assert a.get("/api/designs").status_code == 401
```

Then give the existing module-level client a cookie, so the queue-cap and
old-gate tests further down the file keep working. Change line 19:

```python
client = TestClient(main.app)
```

to:

```python
client = TestClient(main.app)
client.cookies.set("auth", main.COOKIE)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_access.py -q`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'COOKIE'` at import.

- [ ] **Step 4: Add the password, cookie, and middleware to `main.py`**

Add to the stdlib imports at the top (keep them alphabetical, matching the existing block):

```python
import hashlib
import hmac
import time
import urllib.parse
```

Extend the FastAPI import on line 11 and the responses import on line 12. Task 2 trims `Depends` and `Header` back off; leave them for now, the old gate still uses them:

```python
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
```

Then, immediately after `load_dotenv()` and the `BASE` assignment (around line 25), add:

```python
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()
if not PASSWORD:
    raise SystemExit(
        "DASHBOARD_PASSWORD is not set, so there would be no lock on the door.\n"
        "Add this line to your .env file:\n\n"
        "    DASHBOARD_PASSWORD=pick-something-only-you-two-know\n\n"
        "then start the dashboard again."
    )
# The cookie is the hash of the password itself: no session store to keep, and
# changing the password in .env signs everyone out for free.
COOKIE = hashlib.sha256(PASSWORD.encode()).hexdigest()
OPEN_PATHS = {"/login", "/logout", "/static/styles.css"}
```

Add after the `/` route (after line 45), replacing nothing:

```python
@app.middleware("http")
async def require_login(request: Request, call_next):
    """Gate everything but the login page - including the StaticFiles mounts."""
    if request.url.path not in OPEN_PATHS and not hmac.compare_digest(
        request.cookies.get("auth", ""), COOKIE
    ):
        # the dashboard JS expects JSON from /api/*, not a page of HTML
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


def _login_page(error: str = ""):
    with open(os.path.join(BASE, "static", "login.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read().replace("<!--error-->", error))


@app.get("/login")
def login_page():
    return _login_page()


@app.post("/login")
async def login(request: Request):
    # ponytail: parse_qs instead of fastapi.Form, which would pull in
    # python-multipart for one field on one endpoint
    form = urllib.parse.parse_qs((await request.body()).decode())
    entered = (form.get("password") or [""])[0]
    if not hmac.compare_digest(
        hashlib.sha256(entered.encode()).hexdigest(), COOKIE
    ):
        time.sleep(1)  # enough to make guessing over a tunnel pointless
        return _login_page("Incorrect password")
    r = RedirectResponse("/", status_code=303)
    # cloudflared terminates TLS and forwards plain http, so trust its header;
    # falling back to the scheme keeps the cookie usable on http://localhost
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    r.set_cookie("auth", COOKIE, max_age=2592000, httponly=True,
                 samesite="lax", secure=proto == "https")
    return r


@app.post("/logout")
def logout():
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie("auth")
    return r
```

- [ ] **Step 5: Create the login page**

Create `static/login.html`. `.eyebrow`, `.rule` and `.gem` are styled under
`.brand` in `styles.css`, so the wordmark block reuses that class verbatim.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compound</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Italiana&family=Outfit:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/styles.css">
</head>
<body class="login">
  <form class="login-card" method="post" action="/login">
    <div class="brand">
      <p class="eyebrow">Print design house</p>
      <h1>Compound</h1>
      <div class="rule" aria-hidden="true"><span class="gem"></span></div>
    </div>
    <input type="password" name="password" placeholder="Password" autofocus required>
    <button class="gilt" type="submit">Enter</button>
    <p class="login-error"><!--error--></p>
  </form>
</body>
</html>
```

- [ ] **Step 6: Style it**

Append to `static/styles.css`. The animated SVG marble from `index.html` is
deliberately not duplicated here — flat marble only.

```css
/* ── login ─────────────────────────────────── */
body.login { display: flex; align-items: center; justify-content: center; }
.login-card {
  width: 320px; max-width: calc(100% - 32px);
  padding: 34px 30px 26px; text-align: center;
  background: rgba(19, 16, 13, 0.82);
  border: 1px solid var(--mist);
}
.login-card .brand { border-bottom: none; padding-bottom: 8px; }
.login-card input[type=password] {
  width: 100%; margin: 22px 0 14px;
  text-align: center; letter-spacing: 0.2em;
}
.login-card button { width: 100%; }
.login-error {
  min-height: 17px; margin: 14px 0 0;
  font-size: 12px; color: var(--clay);
}
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_access.py -q`
Expected: PASS, all tests including the pre-existing ones.

- [ ] **Step 8: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. The other test files call endpoint functions directly rather than over HTTP, so the middleware never runs for them.

- [ ] **Step 9: Commit**

```bash
git add main.py static/login.html static/styles.css tests/conftest.py tests/test_access.py
git commit -m "feat: gate the whole dashboard behind a login page"
```

---

### Task 2: Delete the old access-code gate

**Files:**
- Modify: `main.py` (the `require_access_code` function at 48-52, its 14 `Depends` call sites, `SettingsBody` at 85, `/api/status` at 435, plus the startup block at 32-35)
- Modify: `tests/test_access.py` (delete the tests for the removed gate)

**Interfaces:**
- Consumes: `main.COOKIE` from Task 1.
- Produces: `/api/status` no longer returns an `access_code` key; `SettingsBody` no longer accepts `access_code`. Task 3's frontend depends on both.

- [ ] **Step 1: Delete the old tests**

From `tests/test_access.py`, delete these ten functions outright — they test a gate that is about to stop existing:

- `test_generation_open_when_no_code_set` (line 28)
- `test_generation_blocked_without_code_header_when_code_set` (34)
- `test_generation_blocked_with_wrong_code` (41)
- `test_generation_allowed_with_correct_code` (49)
- `test_reading_designs_never_gated` (57) — reads *are* gated now, and Task 1's `test_api_refused_without_cookie` covers the replacement
- `test_settings_open_when_no_code_set` (64)
- `test_settings_gated_once_code_set` (71)
- `test_data_exports_open_when_no_code_set` (85)
- `test_data_exports_gated_once_code_set` (91)
- `test_all_mutating_design_actions_gated_when_code_set` (121)

Also delete the `DATA_EXPORTS` module constant at line 82.

Keep the four queue-cap tests — `test_queue_cap_rejects_when_full`,
`test_queue_cap_allows_below_limit`, `test_regenerate_respects_queue_cap`,
`test_queue_cap_rejects_generate_when_full` — and everything added in Task 1.

Replace the deleted export tests with one that proves the exports are still
gated, now by the cookie:

```python
def test_data_exports_need_a_cookie():
    _reset()
    a = _anon()
    for path in ("/api/export.csv", "/api/backup"):
        assert a.get(path).status_code == 401, f"{path} not gated"
        assert client.get(path).status_code == 200, f"{path}: refused a signed-in client"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_access.py -q`
Expected: PASS actually — deleting tests cannot fail. This step exists to confirm you deleted only the intended ones and the file still imports. If anything errors, you removed something the remaining tests needed.

- [ ] **Step 3: Remove the gate from `main.py`**

Delete the whole function at lines 48-52:

```python
def require_access_code(x_access_code: str | None = Header(default=None)) -> None:
    """Gate generation once a shared code is set; open when no code exists."""
    code = db.get_setting("access_code")
    if code and x_access_code != code:
        raise HTTPException(401, "Access code required")
```

Delete the `_gate: None = Depends(require_access_code)` parameter from every
route that has it. There are 14: `generate`, `generate_test`, `delete_design`,
`approve`, `reject`, `retry`, `regenerate`, `unreview`, `publish`,
`save_settings`, `export_csv`, `backup`, and the two `/api/test/*` routes if
they carry it. Where it is the only parameter, the route function ends up with
an empty parameter list — that is correct.

Find them all with:

```bash
grep -n "require_access_code\|Depends" main.py
```

Then drop `Depends` and `Header` from the FastAPI import on line 11 if nothing
else in the file uses them (check with the same grep):

```python
from fastapi import FastAPI, HTTPException, Request
```

Delete `access_code: str = ""` from `SettingsBody` (line 85), and the
`"access_code": bool(db.get_setting("access_code")),` line from the `/api/status`
return (line 435).

- [ ] **Step 4: Drop the stale code from the database**

In the startup block at lines 32-35, alongside the existing requeue statement,
add:

```python
    # the header gate is gone; leaving the row would keep shipping the old code
    # in plaintext inside every /api/backup zip
    con.execute("DELETE FROM settings WHERE key = 'access_code'")
```

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. If `test_api.py` fails on a missing `_gate` argument, you missed a `Depends` removal — that file calls the route functions directly.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_access.py
git commit -m "refactor: drop the X-Access-Code gate now the cookie replaces it"
```

---

### Task 3: Frontend cleanup

**Files:**
- Modify: `static/app.js` (107-165, 267-296, 813-814)
- Modify: `static/index.html` (57-60 sidebar footer, 195-205 access-code panel, 208-217 data panel)

**Interfaces:**
- Consumes: `POST /logout` and the `auth` cookie from Task 1; the removed `access_code` fields from Task 2.
- Produces: nothing later tasks depend on.

- [ ] **Step 1: Gut `apiFetch` in `app.js`**

The cookie rides along on every request automatically, so the header dance goes.
Replace lines 107-145 — from the `// One prompt per burst:` comment through the
closing brace of `apiFetch`, which also removes `askForCode` and the `codeAsk`
variable — with:

```js
// The auth cookie rides along automatically; a 401 means it expired or was
// cleared, and the only cure is the login page.
async function apiFetch(path, opts) {
  const r = await fetch(path, opts);
  if (r.status === 401) {
    location.href = "/login";
    throw new Error("Signed out");
  }
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return r;
}
```

Leave the `let tab = ...` and `let stat = {}, busy = 0;` declarations above line
107 alone — the rest of the file uses them.

- [ ] **Step 2: Delete `downloadFile`**

Delete the whole function at lines 150-165, including its two-line comment. The
missing header was its only reason to exist.

- [ ] **Step 3: Delete the access-code plumbing from `saveSettings` and `forgetCode`**

In `saveSettings` (line 268), delete the `const code = ...` line, the
`access_code: code,` body field, the `if (code.trim()) localStorage.setItem(...)`
line and its comment, and the
`document.getElementById("access_code").value = "";` reset line.

Delete the whole `forgetCode` function (lines 292-295).

- [ ] **Step 4: Delete the status hint**

Delete these two lines from the status handler (813-814):

```js
    document.getElementById("code_state").textContent =
      status.access_code ? "code set ✓ — link is gated" : "no code — anyone with the link can queue";
```

- [ ] **Step 5: Remove the Shared link panel from `index.html`**

Delete the entire `<section class="panel">` containing the "Shared link" label,
the `access_code` input, and the "Forget code on this device" button (lines
195-205).

- [ ] **Step 6: Turn the download buttons back into links**

In the "Your data" panel, replace the two buttons:

```html
          <button onclick="downloadFile('/api/export.csv', 'compound-designs.csv')">Export designs (CSV)</button>
```
```html
          <button onclick="downloadFile('/api/backup', 'compound-backup.zip')">Back up everything</button>
```

with plain links, which now work because the cookie is sent on ordinary
navigations:

```html
          <a class="button" href="/api/export.csv" download>Export designs (CSV)</a>
```
```html
          <a class="button" href="/api/backup" download>Back up everything</a>
```

Add to the end of `static/styles.css` so the links match the buttons around them:

```css
a.button { display: inline-block; text-decoration: none; }
```

and extend the existing `button {` selector on line 148 to `button, a.button {`.

- [ ] **Step 7: Add the log out link**

In the sidebar footer (line 58), below the status bar:

```html
    <div class="side-foot">
      <div id="statusbar"><span class="dot"></span><span id="status_text">loading…</span></div>
      <form method="post" action="/logout"><button class="linky" type="submit">Log out</button></form>
    </div>
```

and to `static/styles.css`:

```css
.side-foot form { margin: 10px 0 0; }
button.linky {
  border: none; padding: 0; font-size: 10px; letter-spacing: 0.2em;
  color: var(--stone); background: none;
}
button.linky:hover { color: var(--gold-leaf); }
```

- [ ] **Step 8: Check nothing still references the removed names**

Run:

```bash
grep -rn "accessCode\|access_code\|downloadFile\|forgetCode\|code_state\|X-Access-Code" static/ main.py
```

Expected: no output. Any hit is a leftover — remove it.

- [ ] **Step 9: Run the suite and the app**

Run: `.venv/bin/pytest -q`
Expected: PASS.

Then start the server and check by hand, since none of this is covered by tests:

```bash
DASHBOARD_PASSWORD=letmein .venv/bin/uvicorn main:app --port 8000
```

Open `http://localhost:8000`, confirm you land on the login page, that a wrong
password says "Incorrect password", that the right one gets you the dashboard,
that Export CSV and Back up everything both download, and that Log out returns
you to the login page.

- [ ] **Step 10: Commit**

```bash
git add static/app.js static/index.html static/styles.css
git commit -m "feat: log out link, and drop the access-code UI the cookie replaces"
```

---

### Task 4: Update the README

**Files:**
- Modify: `README.md` (the "Share with a few people" section, lines 111-129)

**Interfaces:**
- Consumes: the finished behaviour from Tasks 1-3.
- Produces: nothing.

- [ ] **Step 1: Rewrite the sharing steps**

Replace step 1 and the paragraph after step 3 (lines 115-129). The current text
describes the deleted header gate: that a code is set in Settings, that it only
covers generation and review, that the link is open without one, and that people
are asked for the code "on first generate".

```markdown
1. Add a password to `.env` on this machine:

       DASHBOARD_PASSWORD=pick-something-only-you-two-know

   The dashboard refuses to start without one. It is the only way in - the
   designs, the images, and the settings are all behind it, not just the
   buttons. Change it by editing this line and restarting; that also signs
   out everyone who was already in.
2. Install `cloudflared` from Cloudflare's site.
3. Run:

       .venv\Scripts\python share.py

That starts the server, opens the tunnel, and prints a
`https://<random>.trycloudflare.com` URL. Share it, and share the password
separately. Anyone who opens the link gets a login page; once past it they stay
signed in on that browser for 30 days, or until you change the password. Ctrl-C
stops both the server and the tunnel.
```

- [ ] **Step 2: Check for other stale mentions**

Run:

```bash
grep -n -i "access code" README.md docs/*.md 2>/dev/null
```

Expected: no output outside `docs/superpowers/`, which is a historical record and stays as written.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README describes the login page, not the old access code"
```
