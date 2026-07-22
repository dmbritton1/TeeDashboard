# Dashboard Reorganization — Design Spec

Date: 2026-07-04
Status: draft, awaiting user approval

## Overview

Reorganize the single-page t-shirt dashboard into a four-tab in-house business tool, keeping the black-marble/gold visual identity and the existing FastAPI + SQLite + vanilla-JS stack (no frameworks, no build step, no new dependencies).

**Tabs:** Dashboard (stats + review queue), Add designs, Library (full archive with search), Settings.

## Goals

- Stats with graphs: pipeline snapshot, quality rates, style breakdown.
- Add-designs tab: paste input, CSV upload, saved editable AI-prompt field with copy button, duplicate warning.
- Library tab: every design ever made, searchable with filters, tags (auto + manual), star ratings, Printify links, print-file download, lightbox.
- Settings tab: API keys with test-connection buttons, export, backup.
- Review workflow upgrades: bulk actions, keyboard mode, variation compare, undo, cancel queued, delete rejected, tab-title badge.
- Fix: persist the Printify product id on publish (currently discarded).

## Non-goals

- Sales/order data from Printify, social posting, scheduling, multi-user, style presets (deferred; user declined presets).
- Remix-from-Library and shirt-color preview (explicitly deferred to a future project).

## Current state

- `main.py`: FastAPI, endpoints for generate/list/approve/reject/retry/regenerate/publish/settings/status. `printify.publish()` returns `product_id` which is thrown away.
- `db.py`: SQLite `designs(id, phrase, filters, file, print_file, status, error, created_at)`, `settings(key, value)`, `usage(day, images)`.
- `static/index.html`: one file with all markup/CSS/JS. Black marble + gold design (Italiana/Outfit), sidebar with pipeline stages, 3-second polling of `/api/status` + `/api/designs`.
- Statuses: queued → generating → pending → approved → published, plus failed, rejected.

## Architecture decisions

1. **Client computes stats.** The client already polls the full designs list every 3s. All charts and counts derive from that list in JS — no `/api/stats` endpoint, one source of truth. Ceiling: fine up to ~10k designs; if it ever lags, add a server-side stats endpoint then.
2. **CSV parsed in the browser.** File input → JS parse → same `/api/generate` text path as paste. No upload endpoint. Header row: skip row 1 iff its first cell case-insensitively equals `phrase` (no fuzzier heuristics).
3. **Duplicate warning client-side.** Before queueing, compare new phrases (case-insensitive, trimmed) against the already-loaded designs list; show a confirm listing duplicates with options to skip them or queue anyway.
4. **Tags = derived + stored.** Auto tags are `filters` split on commas (computed at render, never stored). Manual tags live in a new `tags` column (comma-separated text). A design's effective tag set = auto ∪ manual. No backfill needed.
5. **Hash routing.** `#dashboard`, `#add`, `#library`, `#settings` so refresh/back keep the tab.
6. **Split static files.** `static/index.html` (shell + tab containers), `static/styles.css`, `static/app.js`. Same design language; no visual redesign.
7. **Charts are hand-rolled inline SVG** (donut, horizontal bars, weekly bar series) in the gold-on-black palette. No chart library.

## Schema changes (SQLite `ALTER TABLE ... ADD COLUMN`, applied idempotently in `db.init()`)

```sql
ALTER TABLE designs ADD COLUMN tags TEXT NOT NULL DEFAULT '';        -- manual tags, comma-separated
ALTER TABLE designs ADD COLUMN rating INTEGER NOT NULL DEFAULT 0;    -- 0 = unrated, 1-5 stars
ALTER TABLE designs ADD COLUMN product_id TEXT;                      -- Printify product id, set on publish
ALTER TABLE designs ADD COLUMN reviewed_at TEXT;                     -- set on approve/reject, cleared on unreview
```

## API changes (`main.py`)

| Change | Detail |
|---|---|
| `PATCH /api/designs/{id}` | Body `{tags?: string, rating?: int}`. Updates manual tags / rating. 404 if missing. Rating clamped 0–5. |
| `DELETE /api/designs/{id}` | Allowed only for status `queued`, `rejected`, `failed` (409 otherwise). Deletes row and any `file`/`print_file` on disk. Serves both "cancel queued" and "delete rejected". |
| `POST /api/designs/{id}/unreview` | `approved` or `rejected` → `pending`; clears `reviewed_at`. Guarded like other transitions. |
| `approve`/`reject` | Also set `reviewed_at = datetime('now')`. |
| `publish` | Persist returned `product_id` in the new column. |
| `POST /api/test/gemini` | Makes a minimal real call with the stored key (e.g. list models); returns `{ok, message}` — message is the provider's error text when it fails. |
| `POST /api/test/printify` | GET the shop via stored token+shop id; returns `{ok, message}`. |
| `GET /api/export.csv` | Streams CSV of all designs: id, phrase, style, status, tags, rating, product_id, created_at. |
| `GET /api/backup` | Zips `designs.db` + the `designs/` image folder (stdlib `zipfile`, written to a temp file) and returns it as `atelier-backup-YYYY-MM-DD.zip`. |
| `GET/POST /api/settings` | Add `prompt_template` (stored in `settings` table). Unlike keys, GET returns its full text (it is not a secret). Existing keys still return booleans. |
| `GET /api/designs` | Include new columns in the row dicts (automatic via `SELECT *`). |

Everything else is untouched. The worker, pipeline, upscale, and printify modules do not change (except `publish` capturing the product id in `main.py`).

## Tab designs

### 1. Dashboard (`#dashboard`)

Top: **snapshot strip** — four stat cards (awaiting review, in queue, approved & unpublished, failed) with big Italiana numerals; each card is a shortcut that scrolls to the review queue filtered to that stage.

Middle: **two charts side by side** (stacked on mobile):
- *Quality rates*: horizontal stacked bar per calendar week (last 8 weeks with data): share approved (incl. published) vs rejected, from `reviewed_at` (fallback `created_at` for legacy rows). Text label with the approval %.
- *Style breakdown*: top 8 effective tags by design count; each row shows tag, count bar, and approval % for that tag.

Bottom: **review queue** — the existing stage plaques (To review / In press / Approved / Published / Failed / Rejected) and card grid, moved here unchanged, plus the Stage-6 workflow upgrades.

### 2. Add designs (`#add`)

- **AI prompt box**: textarea pre-filled from `prompt_template` setting (first run defaults to: "Give me 20 t-shirt design ideas for [niche]. Format each as one line: phrase | style keywords. Example: reel cool dad | vintage, distressed, lake colors"), auto-saved on edit (debounced POST), "Copy prompt" button using `navigator.clipboard` with a "copied ✓" confirmation. Sits above the input area with copy explaining the loop: copy prompt → paste into ChatGPT/Claude → paste results below.
- **Paste input**: the existing `phrase | style` textarea + Generate button.
- **CSV upload**: file input accepting `.csv`; two columns phrase,style; optional header row skipped; rows previewed as a count ("14 ideas found, 2 possible duplicates") before a confirm-queue button.
- **Duplicate warning** applies to both paste and CSV paths.

### 3. Library (`#library`)

- Grid of all designs (any status), newest first, each card: image (or placeholder), phrase, status chip, tag chips, star rating (clickable 1–5, PATCH on click), created date.
- **Search bar**: free-text over phrase + style text.
- **Filters row**: status multi-select chips; tag multi-select (chips sourced from all effective tags, AND semantics); minimum-rating selector; date range (from/to); sort (newest, oldest, rating, A–Z).
- All filtering client-side over the polled list.
- **Card detail (lightbox)**: click image → full-size overlay with phrase, style, tags editor (add/remove manual tags), rating, error text if any, buttons: download print file (link to `/designs/...` when present), open Printify product (when `product_id`; link `https://printify.com/app/products/{product_id}` — if Printify changes their URL scheme the id is still shown with a copy button), and the status-appropriate actions (retry, unreview, delete).
- Esc/overlay-click closes; keyboard ←/→ moves between results.

### 4. Settings (`#settings`)

- Gemini key, Printify token, shop id (existing behavior: write-only fields, saved state shown as booleans) — each with a **Test** button calling the new test endpoints, showing plain-language results.
- **Export library (CSV)** button → `/api/export.csv`.
- **Back up everything** button → `/api/backup` (browser download). Caption states what's inside.
- Generation info: variations per line, daily cap / local GPU status (read-only text from `/api/status`).

### Review workflow upgrades (on Dashboard)

- **Bulk actions**: checkbox on pending cards; select-all; bulk Approve / Reject buttons appear when any selected (loop over existing endpoints client-side).
- **Keyboard mode**: when on the To-review stage, ←/→ (or j/k) move a highlight ring between cards; A approves, R rejects, U undoes the last action (calls unreview), Space opens the lightbox. A small legend shows the keys. Disabled while typing in inputs.
- **Variation compare**: pending cards with the same phrase+filters render grouped side by side under one shared header.
- **Undo**: last approve/reject action gets a toast with an Undo button (5s) in addition to the U key.
- **Cancel queued** button on queued cards; **Delete permanently** (with confirm) on rejected/failed cards — both use `DELETE /api/designs/{id}`.
- **Tab badge**: `document.title` = "(N) The Atelier" where N = pending count, cleared at zero.

## Error handling

- All new endpoints return FastAPI `HTTPException` with plain-language `detail`; the client keeps the existing `api()` wrapper that surfaces `detail` via alert/toast.
- Clipboard API failure (non-HTTPS/local edge cases): fall back to selecting the textarea text and asking the user to press ⌘C.
- CSV parse errors: show which line failed and why; queue nothing until valid.
- Test endpoints never store anything; they only read stored settings and report.
- File deletion tolerates already-missing files.

## Testing

Follows the repo's existing pytest setup (`tests/`):
- DB migration idempotency (init twice, columns exist once).
- Status-guard tests for DELETE / unreview / PATCH (allowed and 409/404 paths).
- Publish persists product_id (printify mocked).
- Export CSV includes new columns; backup zip contains db + designs dir.
- CSV/paste parsing and duplicate detection are client-side; cover the pure JS functions with a small node-free check page or leave to manual verification via the preview browser (documented in the plan).

## Build stages

Each stage leaves the app fully working and shippable.

1. **Shell reorganization** — split into `index.html`/`styles.css`/`app.js`; hash-routed 4 tabs; existing features relocated (review queue → Dashboard, composer → Add, connections → Settings); Library tab shows the plain unfiltered archive. No new backend.
2. **Backend data layer** — schema migrations (tags, rating, product_id, reviewed_at); PATCH/DELETE/unreview endpoints; reviewed_at on approve/reject; publish saves product_id; prompt_template in settings API; tests.
3. **Dashboard stats** — snapshot strip + quality-rate chart + style-breakdown chart, computed client-side.
4. **Add tab complete** — saved prompt box with copy button; CSV upload with preview; duplicate warnings on both paths.
5. **Library complete** — search, status/tag/rating/date filters, sort, tag editing, star ratings, lightbox with print-file download and Printify link.
6. **Review workflow** — bulk actions, keyboard mode with undo, variation grouping, cancel/delete buttons, tab-title badge.
7. **Settings & safety** — test-connection buttons, CSV export, one-click backup, generation info panel.

## Visual language (unchanged)

Black marble + gold (see `dashboard-design-direction` memory): near-black #14120f, ivory #e9e5dc, gold #c0913a / leaf #e9d08c, Italiana display + Outfit body, live SVG marble background at low opacity, gilded panel edges. New components (stat cards, chips, charts, lightbox, toasts) follow this system.
