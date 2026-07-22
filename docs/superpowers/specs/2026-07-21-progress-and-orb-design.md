# Generation progress bar + persistent activity orb

**Date:** 2026-07-21
**Status:** Approved design, ready for implementation plan

## Goal

Give the operator live feedback that generation is happening:

1. A **real progress bar** on each image while it is being generated, tracking
   FLUX's actual denoising steps.
2. A **persistent activity orb** in the top-right corner, visible on every page,
   that pulses while the worker is cooking and shows how many images remain.

Both are read-only indicators. Neither changes generation behaviour.

## Context

- The FastAPI web server and the background worker run in the **same process**
  (`worker.start()` in `main.py`) and share the one SQLite database, so the
  worker can report progress by writing to the DB and the request handlers read
  it back — no cross-process channel needed.
- The front end already polls `GET /api/designs` and `GET /api/status` every
  3 seconds (`refresh()` in `static/app.js`).
- Generation only runs where a GPU is present (`pipeline.has_local()`), so these
  indicators are inert on non-GPU machines (e.g. the author's Mac). That is
  acceptable and expected.
- Only one design generates at a time (single worker thread), so at most one
  progress bar is ever active.

## Design

### 1. Data flow (backend)

**New column.** Add `progress INTEGER NOT NULL DEFAULT 0` (0–100) to the
`designs` table via the existing migration pattern in `db.py`.

**Reset points.** `progress` is set to `0` whenever a design (re)enters
generation:
- when the worker flips a row to `generating`, and
- in the `main.py` startup requeue that returns orphaned `generating` rows to
  `queued` (so a restarted job never shows stale progress).

**Reporting.** `pipeline.generate_image_local(prompt, on_step=None)` gains an
optional `on_step` callback. Internally it passes FLUX a
`callback_on_step_end` handler that, after each denoising step, computes a
percentage and calls `on_step(pct)`. The worker supplies a callback that writes
`UPDATE designs SET progress = ? WHERE id = ?`.

**Percentage mapping.** With `steps = num_inference_steps` (currently 4), after
completing step `i` (0-based):

```
pct = round((i + 1) / (steps + 1) * 100)   # 4 steps → 20, 40, 60, 80
```

Steps fill only to ~80%, deliberately reserving the top for the VAE-decode tail
that follows the loop. The bar reaches **100% only when the image actually
lands** (row flips to `pending`); the front-end creep (below) covers the gap so
it never reads "100% but still working."

**API.** No endpoint shape changes:
- `GET /api/designs` returns `SELECT *`, so `progress` is included automatically.
- The orb needs only `status.queued` (already returned) plus "is anything
  generating," which the front end derives from the designs list it already
  fetches. `/api/status` is unchanged.

### 2. Progress bar (front end)

- Rendered on a design's **placeholder card while `status === 'generating'`**,
  in both the review grid and the Test tab (wherever an in-flight card shows).
- A slim gold bar whose width is driven by `design.progress`.
- **Gentle creep:** between polls the bar eases forward on its own toward the
  next checkpoint, then snaps to the true polled value as each real step lands.
  Implemented purely in the front end (CSS width transition + a light timer that
  drifts the visual width upward, capped below the next checkpoint and reset to
  the real value on each poll). It always moves but never overtakes reality.
- When the card leaves `generating` (image appears, or it fails), the bar is
  removed with the placeholder.

### 3. Activity orb (front end)

- A single fixed element in `index.html`, placed **outside all `view-*`
  sections** so it persists across hash-route navigation. `position: fixed`,
  top-right.
- Driven from the existing `refresh()`:
  - **Idle** (`queued === 0`): dim, no animation.
  - **Cooking** (`queued > 0`): soft gold pulse, with a small number = the queue
    count (`status.queued`, which counts `queued` + `generating`).
- This is the glanceable, corner-anchored version of the pulse concept already
  hinted at by the status-strip dot; the detailed status text line stays as-is.

## Edge cases

- **Non-GPU machine:** nothing generates; `progress` stays 0, orb stays idle.
- **Restart mid-generation:** startup requeue resets status and `progress` to 0.
- **Failure:** row goes to `failed`; card and bar are removed on the next poll.
- **Multiple designs:** not possible concurrently (single worker), so exactly
  one active bar at a time.
- **Poll latency (≤3s):** acceptable because steps take seconds; the creep masks
  it.

## Testing

- **Unit (backend):** assert the percentage mapping is correct and monotonic for
  representative step counts (e.g. `steps=4` → `[20,40,60,80]`), and that the
  `db` migration adds `progress` with default 0 to a fresh and a pre-existing
  DB. These need no GPU.
- **Manual (front end):** on the GPU machine, queue a batch and confirm the bar
  advances and creeps, and the orb pulses with a live count from every tab; on a
  non-GPU machine, confirm both stay inert.

## Out of scope

- Sub-step / time-interpolated *real* progress (we accept 4 coarse checkpoints
  smoothed by the creep).
- Websocket/SSE realtime (polling is sufficient).
- Orb "needs-attention" colour states (review-waiting / failed) — considered and
  deferred to keep one clear meaning.
