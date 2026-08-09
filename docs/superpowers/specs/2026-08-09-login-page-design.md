# Login page

Replace the partial access-code gate with a real login page, so only the owner
and one friend can reach the dashboard over the shared tunnel link.

## Why the current gate is not enough

`require_access_code` (main.py) checks an `X-Access-Code` header on mutating
endpoints only. Everything else is open to anyone holding the tunnel URL:

- `/` and `/static/*` — the dashboard itself
- `/api/designs`, `/api/status`, `/api/settings`, `/api/styles`, `/api/products`
- `/designs/*` — every generated image, via the `StaticFiles` mount

The code also lives in the `settings` table in plaintext, so `/api/backup` zips
it up alongside the Gemini and Printify keys. And with no code set, the gate is
off entirely: a fresh install is wide open.

## Decisions

- **One shared password**, not per-person accounts. Two people, no need to know
  who did what. Revoking means changing the password and both re-entering it.
- **Password lives in `.env`** as `DASHBOARD_PASSWORD`, not in the database.
  Keeps it out of backup zips and removes the "nothing set means open" hole.
- **Own login page**, not HTTP Basic Auth (ugly native dialog, no logout) and
  not Cloudflare Access (needs a paid domain and a named tunnel).

## Gate

A single `@app.middleware("http")` in `main.py`, running before routing so it
covers the `StaticFiles` mounts too.

Allowed without a cookie:

- `GET /login`, `POST /login`
- `POST /logout`
- `/static/styles.css` (the login page needs it)

Everything else requires a valid `auth` cookie. Without one:

- paths starting with `/api/` → `401 {"detail": "Not signed in"}`, so the
  dashboard JS gets JSON rather than a page of HTML to choke on
- everything else → `303` redirect to `/login`

`require_access_code`, the `X-Access-Code` header, and the `access_code` field
on `SettingsBody` and `/api/status` are deleted. Two gates would be two things
to keep locked.

## Password and cookie

At import time `main.py` reads `DASHBOARD_PASSWORD` from the environment. If it
is empty or missing, the app exits with a message naming the exact line to add
to `.env`. Failing closed matters more than starting.

The cookie is:

- name `auth`, value `sha256(password).hexdigest()`
- `httponly=True`, `samesite="lax"`, `max_age=2592000` (30 days)
- `secure` set from `x-forwarded-proto`, falling back to the request scheme —
  true behind the Cloudflare tunnel, false on plain `http://localhost`, where a
  `Secure` cookie would otherwise be dropped by some browsers

Verification recomputes the hash and compares with `hmac.compare_digest`, so a
wrong guess cannot be timed. Deriving the cookie from the password means no
session store, no secret key, and no expiry bookkeeping — and changing the
password in `.env` invalidates every existing cookie for free.

This applies on `localhost` as well as over the tunnel. One code path, and a
30-day cookie means typing it about once a month.

### Wrong password

`POST /login` sleeps 1 second, then re-renders the login page with an
"Incorrect" message and no cookie. Enough to make guessing over a tunnel
pointless without building lockout state to maintain.

## Login page

`static/login.html`, served by `GET /login` as a `FileResponse`:

- marble background colour and gold accents from the existing `styles.css`
- "Compound" wordmark in Italiana, matching the sidebar brand
- one `type="password"` field, one submit button
- a plain HTML `<form method="post" action="/login">` — no JavaScript, so it
  works even when the dashboard JS is broken

The animated SVG marble veining from `index.html` is **not** duplicated here;
there is no template engine, and it is ~30 lines of SVG for a page you see once
a month. Flat marble colour instead.

`POST /login` on success sets the cookie and returns a `303` to `/`.
`POST /logout` deletes the cookie and returns a `303` to `/login`.

The "Incorrect" variant is produced by a string replace on the same file rather
than a second HTML file.

## Frontend changes

The cookie is sent automatically on every request, which deletes code:

- `askForCode` and the 401-retry-with-header logic in `apiFetch` — `apiFetch`
  collapses to a `fetch` plus error handling, redirecting to `/login` on 401
- `localStorage` code storage and `forgetCode`
- `downloadFile` entirely — Export CSV and Backup become plain `<a href>`
  links, since the missing header was the only reason they could not be
- the Access code input, its Save button, and the `code_state` hint in the
  Settings panel of `index.html`, plus the line in `app.js` that fills it

Added: a "Log out" link in the sidebar footer beside the status line, posting
to `/logout`.

## Cleanup

On startup, `main.py` deletes any `access_code` row from the `settings` table.
It is dead once the header gate is gone, and it is why existing backup zips
carry the code in plaintext.

## Testing

`tests/test_access.py` is rewritten against the new gate:

- no cookie → `/api/designs` returns 401, `/` redirects to `/login`, and a
  `/designs/<file>` image redirects too (that image mount was the real hole)
- correct password → `POST /login` sets an `auth` cookie, and the same requests
  then succeed
- wrong password → no cookie set, page says "Incorrect"
- `POST /logout` clears the cookie and the next request is refused again
- `GET /login` and `/static/styles.css` are reachable with no cookie

New `tests/conftest.py` sets `DASHBOARD_PASSWORD` for the whole test run, since
`main.py` now refuses to import without it.

The other test files call endpoint functions directly rather than over HTTP, so
the middleware never runs for them and they need no changes.

## Out of scope

- Per-person accounts, roles, audit of who did what
- Lockout or rate limiting beyond the 1-second delay
- Password reset flow — the password is a line in `.env` on the owner's Mac
- Encrypting the API keys that `/api/backup` still zips in plaintext
