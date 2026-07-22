# Gemma Prompt Refinement — Design

**Date:** 2026-07-21
**Status:** Approved for planning

## Goal

Insert a Gemma text-model step between the user's input and image generation. It
takes each line's **search term (phrase)** and **filters (style words)** and
writes richer, original image-generation prompts — guided by a **user-editable
system prompt**. The user tunes *how Gemma thinks* (its role and rules) in an
editable box; Gemma then invents the actual prompts on its own from that brief
plus the per-line input.

## Key behaviors (locked with user)

1. **Model source:** Gemini API's Gemma model (`gemma-3-27b-it`) via the already
   installed `google-genai` and the existing `GEMINI_API_KEY`. Gemma is a text
   model on a **separate quota** from image generation, so it works even while
   the image quota is capped. No local model (keeps the 8GB Mac free).
2. **One distinct prompt per variation.** Asking for N variations of a line
   produces N genuinely different creative prompts from a single Gemma call.
3. **T-shirt rules baked into the system prompt.** Gemma's output goes *straight
   to FLUX* — the old fixed `build_prompt` template is bypassed when refinement
   runs. The default system prompt carries the "t-shirt graphic / plain solid
   background / no mockup / no watermark" rules.
4. **On/off checkbox** ("Refine with Gemma", on by default) lets the user A/B
   raw-template vs Gemma while tuning the system prompt.
5. **Empty-box fallback:** checkbox on but box empty → use built-in
   `DEFAULT_REFINE_PROMPT`, never send Gemma an empty system prompt.
6. **Error fallback:** if Gemma fails (no key, quota, network, unparseable
   output) → fall back to the existing `build_prompt` path so the queue never
   breaks, and tell the user refinement was skipped.

## Architecture

### New module `refine.py`

```
DEFAULT_REFINE_PROMPT: str   # the starting system prompt (t-shirt rules + creative brief)
GEMMA_MODEL = "gemma-3-27b-it"  # one constant, easy to swap

def refine(phrase: str, filters: str, n: int, system_prompt: str) -> list[str]:
    """One Gemma call → n distinct ready-to-generate image prompts.
    Raises on API error or when fewer than 1 prompt can be parsed."""
```

- Builds a user message from `phrase` + `filters`, asks for exactly `n` numbered
  prompts.
- Parses the numbered/newline-separated response into a clean `list[str]`.
- Callers handle failure (see `/api/generate` fallback). `refine` itself does not
  swallow errors.
- **Self-check:** `__main__` block with asserts on the parse helper (N numbered
  lines in → N clean strings out; strips numbering/blank lines). No test
  framework.

`DEFAULT_REFINE_PROMPT` content (starting text, fully editable later):
> You are an art director for a print-on-demand t-shirt brand. Given a concept
> and optional style keywords, write {n} distinct, vivid image-generation
> prompts — each a different creative interpretation. Use the style keywords as
> creative direction. Every prompt must describe standalone artwork on a plain
> solid background: a t-shirt graphic only — no shirt, no mockup, no watermark,
> no text unless the concept requires it. Output only the prompts, numbered 1 to
> {n}, one per line.

### DB change (`db.py`)

One new nullable column via the existing `MIGRATIONS` tuple pattern:

```
("prompt", "ALTER TABLE designs ADD COLUMN prompt TEXT"),
```

`phrase` and `filters` stay populated for Library search, charts, and CSV export.
`prompt` holds the refined text when refinement ran; NULL otherwise.

### Worker change (`worker.py`)

One line — refined prompt wins when present:

```python
text = row["prompt"] or (row["phrase"] if row["test"] else pipeline.build_prompt(row["phrase"], row["filters"]))
```

No other worker changes. Refinement happens at queue time (server has the key),
so the GPU box just consumes stored prompts.

### API change (`main.py`)

- `GenerateBody` gains `refine: bool = True`.
- `SettingsBody` gains `refine_prompt: str = ""` (persisted like `prompt_template`).
- `GET /api/settings` returns `refine_prompt` (falling back to
  `refine.DEFAULT_REFINE_PROMPT` when unset, mirroring how `prompt_template`
  falls back to `DEFAULT_PROMPT`).
- `POST /api/generate` new flow per parsed line:

```
filters = style_filters(body.style, filters)
if body.refine:
    system_prompt = db.get_setting("refine_prompt") or refine.DEFAULT_REFINE_PROMPT
    try:
        prompts = refine.refine(phrase, filters, body.variations, system_prompt)
    except Exception:
        prompts = None            # fall back below; flag it in the response
    if prompts:
        for p in prompts:
            insert(phrase, filters, prompt=p, status='queued')
        continue
# fallback / refine off: original path, N rows, prompt = NULL
for _ in range(body.variations):
    insert(phrase, filters, status='queued')
```

Response reports `{"queued": N, "refined": bool}` so the UI can say when
refinement was skipped. Queue-full (429) and access-code gate unchanged.

### UI (`static/index.html`, `static/app.js`)

In the **Add** view, below the "Commission ideas" input:

- A collapsible `<details>` disclosure: **"Creative refinement (advanced)"**
  containing:
  - `<textarea id="refine_box">` — the editable system prompt, auto-saved to
    setting `refine_prompt` on input (debounced), reusing the exact
    `prompt_box` save pattern (`loadPrompt` / input listener). Loaded from
    `/api/settings.refine_prompt`.
  - A short hint: "How Gemma rewrites your lines into creative prompts. Leave
    blank to use the built-in default."
- A `<input type="checkbox" id="refine_toggle" checked>` labeled **"Refine with
  Gemma"** in the generate row.
- `generate()` sends `refine: document.getElementById("refine_toggle").checked`
  in the POST body, and if the response has `refined === false` while the toggle
  was on, flashes "Gemma refinement was skipped — generated from the basic
  template instead."

## Data flow

```
User line "funny fishing shirt | vintage, distressed"  +  variations=2  +  refine=on
        │
        ▼  /api/generate
style_filters() → filters
        │
        ▼  refine.refine(phrase, filters, 2, system_prompt)   [Gemini API / Gemma]
["<creative prompt A>", "<creative prompt B>"]
        │
        ▼  insert 2 rows: phrase, filters kept; prompt = A / B; status=queued
        ▼  worker: text = row["prompt"]  →  FLUX  →  PNG
```

Failure at the Gemma step → 2 rows with `prompt=NULL` via `build_prompt`, and the
UI notes refinement was skipped.

## Out of scope (YAGNI)

- Per-output prompt editing before generation — the user tunes the *system*
  prompt; generated images already have a review/approve stage.
- Making the model name a setting — it's a constant with a comment.
- Retrofitting existing queued rows.
- Streaming / showing Gemma's raw response in the UI.

## Testing

- `refine.py` `__main__` assert self-check on the response parser.
- Manual: generate with refine on (verify distinct prompts stored in `prompt`),
  refine off (verify NULL + template path), and with a bad/absent key (verify
  fallback + "skipped" message).
