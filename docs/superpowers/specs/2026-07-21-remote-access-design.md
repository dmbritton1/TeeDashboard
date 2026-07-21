# Remote access: queue images from any device

## Goal

Let a few known people open the dashboard from any device and queue images,
with the GPU generation still running locally on this machine. Simple to share
(a link), lightly protected so a leaked link can't tie up the GPU.

## Why not GitHub Pages

GitHub Pages serves static files only — it can't run the FastAPI server, the
SQLite DB, the worker, or FLUX on the GPU. Generation is pinned to this machine's
Radeon. So "remote access" means exposing the local server, not moving it.

## Architecture

Two independent parts:

1. **Exposure (ops, no code):** a Cloudflare Quick Tunnel fronts the local server.
   - `cloudflared tunnel --url http://localhost:8000` returns a random
     `https://*.trycloudflare.com` URL.
   - Server runs with `--host 0.0.0.0` so the tunnel can reach it.
   - URL changes each tunnel restart. A stable URL (named tunnel + account) is a
     deferred upgrade, out of scope here.

2. **Protection (app code):** a shared access code plus a queue cap.

## Component 1: Shared access code

A single passphrase gates image generation. No accounts, no per-user login.

- **Storage:** reuse the `settings` table via `db.set_setting`/`get_setting` with
  key `access_code`. Set/changed from the existing settings panel.
- **Enforcement:** a FastAPI dependency checks an `X-Access-Code` header against
  the stored code. Applied to the generation endpoints (`/api/test`, and
  `/api/generate` for consistency). Read-only endpoints (`/api/designs`,
  `/api/status`) stay open so the page can load and show the code prompt.
- **Behavior when no code is set:** enforcement is off (open), so nothing breaks
  for the current local-only workflow. The gate activates only once a code exists.
- **Frontend:** on a 401 from a generation call, prompt for the code, store it in
  `localStorage`, and send it as `X-Access-Code` on every request. A "forget code"
  affordance clears it.

### Interface

- `db.get_setting("access_code")` → the code or None.
- `require_access_code(x_access_code: str | None = Header(None))` → raises
  `HTTPException(401)` when a code is set and the header doesn't match; no-op when
  no code is set.

## Component 2: Queue cap

Prevent a remote person from flooding the single-GPU queue, without blocking the
owner's normal pipeline batches (10 pasted lines = 20 images).

- One coarse ceiling: before inserting in `/api/test` and `/api/generate`, count
  rows with `status IN ('queued', 'generating')`. If ≥ `MAX_QUEUE`, reject with
  `429`.
- `MAX_QUEUE` default **30** — comfortably above a typical 20-image batch, but
  bounds a flood (30 images is already ~5.5h of GPU). Tunable constant next to
  `DAILY_CAP` in `worker.py`.
- Deliberately simple: a single total cap, not per-user (there are no user
  identities). Good enough for "a few known people".

## Data flow

Device → tunnel URL → local FastAPI. Page loads (open endpoints). First generate
→ 401 → browser prompts for code → stores it → retries with `X-Access-Code`.
Server validates, checks the queue cap, inserts a `test=1` row. The existing
worker generates locally and the existing 3s poll shows the result on every device.

## Error handling

- Wrong/missing code on a gated endpoint → 401; frontend re-prompts.
- Queue full → 429; frontend shows "queue is full, try again shortly".
- Tunnel down / server unreachable → existing "server unreachable" status text.
- No code set → gate open (unchanged local behavior).

## Testing

- `require_access_code`: no code set → allows; code set + correct header → allows;
  code set + wrong/absent header → 401. (FastAPI `TestClient`.)
- Queue cap: at/above `MAX_QUEUE` → 429; below → inserts.
- Existing suite stays green; access code absent by default so current tests are
  unaffected.

## Out of scope (YAGNI)

- User accounts / per-user identity.
- Stable tunnel URL / custom domain.
- A GitHub Pages frontend.
- Rate limiting beyond the queue cap.
