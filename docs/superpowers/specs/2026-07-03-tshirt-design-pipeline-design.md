# T-shirt Design Pipeline — Design

**Date:** 2026-07-03
**Status:** Approved approach A (local web app), free-tier generation

## Purpose

A local dashboard for a one-person Etsy t-shirt business: paste a list of
search phrases + style filters, generate design candidates with an image
model, review them in a grid, and publish approved designs to Printify
(which pushes the listing to Etsy).

## Input format

One design concept per line, pasted into a textarea:

```
funny fishing shirt | vintage, distressed, black shirt
plant mom | retro 70s, floral, cream shirt
dog dad | minimalist, line art
```

- Left of `|`: search phrase → the design concept.
- Right of `|`: comma-separated filters → style/color hints folded into the
  image prompt.
- Filters optional; a bare phrase on a line is valid. Blank lines ignored.

## Stack

- **Server:** Python, FastAPI, single `main.py`. Started with one command.
- **DB:** SQLite via stdlib `sqlite3`. No ORM.
- **UI:** one static `index.html`, vanilla JS. Grid of design cards.
- **Image generation:** Gemini 2.5 Flash Image ("Nano Banana") via the
  Google GenAI API **free tier** (~500 images/day, ~2 images/min,
  1024×1024). Key from Google AI Studio, no billing.
- **Upscaling:** Real-ESRGAN run locally (fits 8GB M2), 1024px → ~4096px,
  only on approval.
- **Publishing:** Printify REST API. Printify pushes the product to the
  connected Etsy shop. Not configured until the user creates accounts.
- **Secrets:** `.env` file — `GEMINI_API_KEY` now; `PRINTIFY_API_TOKEN`
  and `PRINTIFY_SHOP_ID` later.

The model call is one swappable function (`generate_image(prompt) -> png
bytes`) so the backend can later point at Replicate (Ideogram/Flux) or a
local model without touching anything else.

## Data flow

1. User pastes list, hits **Generate**.
2. Server parses lines, builds a prompt per phrase:
   "t-shirt design, {phrase}, {filters} style, bold graphic, isolated on
   plain background" (exact wording tunable in one constant).
3. For each phrase, 2 variations are generated via Gemini. Rate limit means
   batches queue in the background; the dashboard polls and fills in as
   images finish.
4. Each image → PNG in `designs/`, row in SQLite:
   `id, phrase, filters, file, status, error, created_at`.
   Status lifecycle: `queued → generating → pending → approved | rejected
   → published`. Failures → `failed` with the error message stored.
5. Dashboard tabs: **Pending** (Approve / Reject / Regenerate per card),
   **Approved** (Publish button), **Published**.
6. **Approve** triggers local Real-ESRGAN upscale of that image.
7. **Publish** (per design or bulk): upload image to Printify, create a
   t-shirt product from a default blueprint with default colors/sizes,
   publish to Etsy. If no Printify token configured, the button is
   disabled with a "Printify not configured" hint and approved designs
   wait in the queue.

## Error handling

- Each line/image is independent: one failure never aborts the batch.
- Failed cards show the API error and a Retry button.
- Rate-limit (429) responses: wait and retry automatically (the free tier
  is 2 images/min, so throttling is expected, not exceptional).

## Testing

One small `test_parsing.py` covering the line parser and prompt builder —
the only real logic. API calls and UI are verified by running the app.

## Costs

$0 at current scale: free Gemini tier for generation, local upscaling.
Daily cap ~500 images. Paid escape hatch: Replicate (Flux-schnell
~$0.003/img drafts, Ideogram ~$0.06/img finals) via the swappable
generate function.

## Out of scope (for now)

- Etsy listing copy generation (title/tags/description) — Printify
  defaults used; can be added later.
- Mockup photo generation, pricing strategy, multi-shop support.
- Auth/hosting — runs locally for one user.
