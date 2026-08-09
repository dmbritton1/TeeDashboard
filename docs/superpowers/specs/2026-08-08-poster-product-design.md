# Posters as a second product

**Date:** 2026-08-08
**Status:** Approved design, ready for implementation plan

## Goal

Let the dashboard produce 50x70cm posters alongside t-shirts, chosen per batch
from a dropdown next to the Art style selector. A poster is a different
**product**, not a different mode: tees and posters live in the same queue, the
same library, and the same review flow, each rendering and publishing according
to what it is.

Nothing about tee generation changes. Every existing design row stays a tee,
every existing prompt path produces the same bytes it produces today.

## Context

The generation seam already exists. On the `poster-size-probe` branch:

- `pipeline.generate_image_local(prompt, on_step=None, size=None)` takes an
  explicit `(width, height)`.
- `POSTER_LADDER` holds seven exact-5:7 sizes, every side divisible by 16.
- `poster_dpi(w, h)` converts a generated size to print resolution on a 50x70cm
  poster after the 4x upscale.
- `_chunk_attention_globally()` slices SDPA over query blocks, which is what made
  any size above 1024 possible on gfx1031 at all (measured 26.6GB -> 2.9GB at
  1440x2016).

**Measured, not assumed:** 960x1344 generates end to end in 19.5 min — about 5s
of denoising and ~19 min of VAE decode — with no tile seams and a clean 1.81MB
PNG. That is 195 dpi on a 50x70cm poster after upscaling. 1440x2016 (292 dpi)
denoises in 8s but its VAE decode ran 23 minutes without finishing.

The decode cost comes from MIOpen: `CK grouped conv library not found for device
gfx1031` means VAE decode falls back to a slow reference convolution. Combined
with tiling, each 128x128 latent tile costs roughly a full 1024^2 decode.

### What is t-shirt-shaped today

| Location | Assumption |
|---|---|
| `pipeline.py` `PROMPT_TEMPLATE` | "Professional t-shirt graphic design... No shirt, no mockup" |
| `refine.py` `DEFAULT_REFINE_PROMPT` | "art director for a print-on-demand t-shirt brand" |
| `worker.py:30,36` | builds the tee template; calls `generate_image_local` with no `size` |
| `printify.py:9` | `BLUEPRINT_ID = 6` (Gildan tee), Black/White only, $24.99, title `"... T-Shirt"` |
| `upscale.py:19` | 4x fixed, and the device is `mps or cpu` — never CUDA/ROCm |
| `pipeline.step_progress` | reserves 1/10 of the bar for decode; a poster spends 97% there |
| `static/styles.css:187,190` | `.card img` and `.placeholder` are `aspect-ratio: 1` |
| `static/index.html:47,151` | "T-shirt design house", "no t-shirt template" |

### Printify reality, checked against the live API

The saved token was queried read-only on 2026-08-08:

```
/catalog/blueprints.json                        200   (1984 blueprints)
/catalog/blueprints/6/print_providers/99/...    200   (tee variants readable)
/catalog/blueprints/1220/print_providers/99/... 401   Unauthenticated
/shops.json                                     401   Unauthenticated
```

Reproducible and selective — same token, same print provider. **Poster variants
and shops are not readable with this token.** Two consequences:

1. The poster blueprint cannot be chosen from facts. Candidates seen at the
   blueprint level: `1220 Rolled Posters`, `852 Vertical and Horizontal Matte
   Posters`, `443 Posters (EU)`, `1697 Satin Posters`, `1309 Satin and Archival
   Matte Posters`. Which carries a 50x70cm variant, and what pixel size it
   demands, is unknown. The blueprint therefore becomes a settings field.
2. `publish()` would fail today **for tees too** — it calls `/shops.json` and
   `variants.json` at publish time. Pre-existing; posters do not introduce it.
   Also, no shop ID has ever been saved, so publish has never run.

`printify.py` has no dedicated test file. Only `test_api.py` touches it, and
only via the settings/test endpoints.

## Design

### 1. Data model

One migration appended to `db.MIGRATIONS`:

```python
("product", "ALTER TABLE designs ADD COLUMN product TEXT NOT NULL DEFAULT 'tee'"),
```

`DEFAULT 'tee'` makes all 93 existing rows correct without a backfill, and any
insert written by code that predates products still lands on a valid value.

### 2. The PRODUCTS registry

In `pipeline.py`, beside the existing `MODELS` registry, which exists for this
exact reason — its comment says the next entry should be "a one-line addition
rather than a rewrite":

```python
PRODUCTS = {
    "tee": {
        "label": "T-shirt",
        "template": PROMPT_TEMPLATE,
        "refine_key": "refine_prompt",
        "size": lambda: (current_size(), current_size()),
        "aspect": "1",
        "decode_share": 1 / (ZIMAGE_STEPS + 1),
        "eta_minutes": 6,
        "blueprint_key": "printify_blueprint_id",
        "blueprint_default": "6",
        "price_cents": 2499,
        "title_suffix": "T-Shirt",
    },
    "poster": {
        "label": "Poster (50x70cm)",
        "template": POSTER_TEMPLATE,
        "refine_key": "refine_prompt_poster",
        "size": poster_size,
        "aspect": "5 / 7",
        "decode_share": 0.95,
        "eta_minutes": 20,
        "blueprint_key": "printify_poster_blueprint_id",
        "blueprint_default": "",
        "price_cents": 3499,
        "title_suffix": "Poster",
    },
}
DEFAULT_PRODUCT = "tee"
```

`product_data(name)` mirrors `current_model()`: an unrecognised name falls back
to `tee` rather than raising, so a bad database value degrades instead of
killing the worker thread.

**Placement matters.** The dict evaluates `PROMPT_TEMPLATE`, `POSTER_TEMPLATE`,
`ZIMAGE_STEPS`, `current_size` and `poster_size` at import time, so `PRODUCTS`
goes after `poster_dpi` (currently line 270) — below everything it names.

The registry holds `refine_key`, a settings key, rather than the prompt text, so
`pipeline` never has to import `refine`. The defaults stay where they already
live, reachable by that key:

```python
# refine.py
DEFAULTS = {
    "refine_prompt": DEFAULT_REFINE_PROMPT,
    "refine_prompt_poster": DEFAULT_REFINE_PROMPT_POSTER,
}
```

`main.generate` resolves `db.get_setting(key) or refine.DEFAULTS[key]`, which is
the same `or`-default shape it uses today, just keyed by product.

Storing `size` as a callable keeps both products uniform and keeps **Speed
Production wired to tees only** — a 512-tall poster is meaningless, so posters
read the ladder instead.

`price_cents` for posters is a placeholder pending the blueprint choice; it is
recorded here so the number lives in one place rather than being invented at
publish time.

### 3. Poster size setting

`poster_size` stores a string like `"960x1344"`.

```python
DEFAULT_POSTER_SIZE = (960, 1344)  # the only rung measured working end to end

def poster_size() -> tuple[int, int]:
    """Which ladder rung posters generate at. Anything not on the ladder falls
    back to the measured default, so a hand-edited setting can't ask the GPU for
    a size we never tested."""
```

The Settings dropdown is built from `POSTER_LADDER`, labelling each rung with
`poster_dpi()` — "960x1344 — 195 dpi". Choosing a rung above the proven one is
allowed and may OOM; that is the accepted cost of making it a setting, and the
failure is visible (status `failed`, torch error on the card, Retry available).

### 4. Prompt templates

```python
# Unlike PROMPT_TEMPLATE this states what it wants rather than what it doesn't:
# Z-Image-Turbo runs at guidance_scale=0.0, so with no classifier-free guidance
# negative phrasing barely binds. The probe demonstrated it - its prompt said
# "no frame, no border" and the output had a prominent frame.
POSTER_TEMPLATE = (
    "Fine-art poster print: {phrase}. "
    "{style}One clear focal subject, upright vertical composition, "
    "artwork running edge to edge and filling the whole canvas, "
    "clear foreground and distant background."
)
```

`refine.DEFAULT_REFINE_PROMPT_POSTER` gets the same treatment — an art director
for a wall-art brand, one focal subject, composed for a tall 5:7 canvas.

`build_prompt(phrase, filters, product="tee")` gains a third argument with a
default, so every existing call site and test keeps passing unchanged.

**Style presets stay shared.** The 30 entries in `STYLE_GROUPS` are art styles,
not garment styles; Art Deco, WPA park poster and Bauhaus arguably suit wall art
better than they suit tees.

### 5. Generation path

`worker.py`:

```python
product = row["product"] or pipeline.DEFAULT_PRODUCT
prompt = row["prompt"] or (row["phrase"] if row["test"]
                           else pipeline.build_prompt(row["phrase"], row["filters"], product))
png = pipeline.generate_image_local(prompt, on_step=on_step,
                                    size=pipeline.product_size(product))
```

`generate_image_local` keeps its `size=None` fallback for other callers and
tests. The worker now always passes a concrete tuple; for tees that tuple is
`(1024, 1024)`, byte-identical to today.

### 6. Progress that tells the truth on a poster

`step_progress` currently reserves `1/(steps+1)` of the bar for the VAE decode.
On a tee the decode is ~5 of ~5.7 minutes and that reserve is roughly right. On
a poster the decode is ~19 of 19.5 minutes: the bar would reach 90% in five
seconds and then freeze for nineteen minutes, which reads as a hung job.

```python
def step_progress(step_index, steps, decode_share=1 / (ZIMAGE_STEPS + 1)):
    return round((step_index + 1) / steps * (1 - decode_share) * 100)
```

This is arithmetically identical to the current formula for tees: with
`steps=9` and `decode_share=0.1`, step 0 gives 10 and step 8 gives 90, exactly
as `(i+1)/(steps+1)*100` does now. Posters pass `decode_share=0.95`, so the loop
fills the first 5% and the decode owns the rest.

A low bar that does not move is still not readable, so the front end changes
too. `card()` already receives the whole design row, and `/api/designs` returns
`SELECT *`, so `product` reaches the client with no endpoint change. `creepTick`
derives its drift rate from the active design's product, advancing a poster
slowly across the decode rather than stalling at 5%, and the working placeholder
shows the expected duration ("decoding — about 20 min").

### 7. Upscale on the GPU

`upscale.py:19` is `torch.device("mps" if torch.backends.mps.is_available()
else "cpu")` — there is no CUDA/ROCm branch, so every upscale on this machine
runs on **CPU**. Pre-existing, and tolerable at 1024^2; a poster is 1.7x the
pixels and produces 3840x5376, which makes it hurt.

```python
device = torch.device("cuda" if torch.cuda.is_available()
                      else "mps" if torch.backends.mps.is_available() else "cpu")
```

ROCm reports as `cuda` in torch, so this covers the RX 6700. Real-ESRGAN 4x on a
960x1344 input may not fit in 10GB; on `torch.OutOfMemoryError` the job retries
on CPU rather than failing. The existing error path is unchanged — the design
stays approved and the message lands in `error`.

### 8. Publishing

`printify.py` becomes product-aware:

- `BLUEPRINT_ID = 6` becomes the tee entry's `blueprint_default`, resolved
  through the product's `blueprint_key` setting. Tees behave identically.
- Variant selection branches per product: tees filter `options.color in COLORS`;
  posters filter `options.size` against `POSTER_VARIANT_MATCH`, a tuple of
  substrings tried in order (`("50x70", "50 x 70", "19.7x27.6")`). The real
  naming is unknown because the variant endpoint 401s, so the match is a list of
  plausible spellings rather than one guess, and both products keep the existing
  `or all_variants[:10]` fallback. Once a token with shop scope exists, this
  constant is the single place to correct.
- `price_cents` and `title_suffix` come from the registry.
- `main.publish` gains a guard mirroring the existing Printify check: an empty
  blueprint setting returns HTTP 400 "Poster blueprint not configured".

**This path cannot be verified end to end** until the token is replaced with one
that has shop scope and a shop ID is saved. It is written against the documented
API shape and covered by tests with `requests` mocked.

### 9. API surface

| Endpoint | Change |
|---|---|
| `GET /api/products` | New. `{name: {label, aspect, eta_minutes}}`, same shape as `/api/styles` |
| `POST /api/generate` | `GenerateBody.product: str = "tee"`; refinement uses that product's `refine_key` |
| `POST /api/test` | `TestBody.product: str = "tee"` |
| `POST /api/designs/{id}/regenerate` | **must carry `product` forward** — it currently copies only phrase and filters, so a regenerated poster would silently become a tee |
| `GET /api/settings` | adds `poster_size`, `poster_sizes` (ladder with dpi), `printify_poster_blueprint_id` (bool) |
| `POST /api/settings` | `SettingsBody` gains `poster_size`, `printify_poster_blueprint_id`, `refine_prompt_poster` |

Both `product` fields are validated against `PRODUCTS` and rejected with HTTP
400 on an unknown value — the request body is the one place a bad product should
be loud rather than silently coerced.

### 10. Front end

**Card shape.** One new rule; the tee rule is untouched:

```css
.card[data-product="poster"] img,
.card[data-product="poster"] .placeholder { aspect-ratio: 5 / 7; }
```

`card()` emits `data-product="${d.product || 'tee'}"`. The lightbox needs no
change — `#lightbox_inner img` has no `aspect-ratio` and already renders natural
shape.

**The toggle.** A `<select id="product_select">` on the Add panel and the Test
panel, populated from `/api/products`, sitting beside the Art style dropdown.
The choice rides on the POST body, **not** a saved setting — per batch, so no
hidden global state makes yesterday's toggle shape today's queue.

**The refine textarea follows the product.** The "Creative refinement
(advanced)" block holds one `#refine_box` bound to `refine_prompt`. With two
products there are two system prompts, so the box loads and saves against the
selected product's `refine_key`, swapping content when the dropdown changes.
Switching products with unsaved edits saves them to the product they were
written for, not the one being switched to.

**Settings.** Poster size dropdown under Generation; Poster blueprint ID beside
Shop ID under Connections.

**Copy.** `index.html:47` "T-shirt design house" and `:151` "no t-shirt
template" become product-neutral.

### 11. Error handling

Every failure degrades rather than throws, matching the fallback habit
`current_model()` already established:

| Failure | Behaviour |
|---|---|
| Unknown product in the database | falls back to `tee` |
| Unknown product in a request body | HTTP 400 |
| Unparseable or off-ladder `poster_size` | falls back to `DEFAULT_POSTER_SIZE` |
| Poster blueprint not configured at publish | HTTP 400 with a readable message |
| A ladder rung that OOMs | design goes `failed` with the torch error; Retry already exists |
| Upscale OOM on GPU | retries on CPU; unchanged error path if that fails too |

### 12. Testing

House style throughout: plain pytest functions, `monkeypatch`, no fixture files,
no classes — as in `tests/test_pipeline.py`.

- **`test_pipeline.py`** — registry integrity (every product carries every key);
  `product_data` fallback; `poster_size` parsing, off-ladder rejection and
  default; `build_prompt` per product and its default argument; `product_size`
  returning the square tee tuple and honouring Speed Production; `step_progress`
  producing byte-identical values to the old formula for tees.
- **`test_worker.py`** — the worker passes the right size and the right template
  per product, and an unknown product still generates as a tee.
- **`test_api.py`** — product validation on both bodies; regenerate carries
  `product` forward; settings round-trip the new keys.
- **`test_printify.py`** — new file, `requests` mocked: blueprint resolution per
  product, variant filtering per product, title and price, and the
  not-configured guard.
- **`test_db.py`** — the migration adds `product` defaulting to `tee`.

## Open question, deliberately not resolved here

The probe's 960x1344 output was a 2x2 mirrored ornament rather than a poster with
a subject — measured mirror symmetry 20.5 left-right and 38.1 top-bottom against
a 55.8 random baseline. That matches the known "duplicated subject at unfamiliar
aspect ratio" failure mode.

It is **confounded and not conclusive**: the probe's own prompt asked for
"geometric symmetrical ornamental structure", so the model produced what it was
asked for, and there was no subject to duplicate because none was requested.

Settling it needs one ~20 minute GPU run at 960x1344 with a single-subject prompt
and no symmetry language. This is an explicit implementation task with a decision
point, not a blocker on the architecture — the seam is identical either way. If
the subject duplicates, `POSTER_TEMPLATE` needs composition guardrails and this
spec's template is where they go.

## Non-goals

- **`main.DEFAULT_PROMPT`** (the idea prompt you copy into ChatGPT) stays
  tee-worded. It is user-editable, you have already customised it, and making it
  per-product doubles a setting for a clipboard helper.
- **A poster-only style list.** The existing presets are art styles and serve
  both products.
- **Changing `MAX_QUEUE`.** 30 queued posters is roughly 10 hours, but the fix
  for that is the ETA the progress work already surfaces, not a smaller queue.
- **Choosing the poster blueprint.** Blocked on a token with shop scope; the
  settings field is the deliverable.
- **Proving the publish path.** Blocked on the same token, for tees as much as
  posters.
