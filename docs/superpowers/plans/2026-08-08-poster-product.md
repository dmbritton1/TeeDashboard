# Posters as a Second Product Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the dashboard generate, review and publish 50x70cm posters alongside t-shirts, chosen per batch from a dropdown, without changing anything about how t-shirts behave.

**Architecture:** A `product` column on `designs` plus a `PRODUCTS` registry in `pipeline.py` beside the existing `MODELS` registry. Eight seams (prompt template, Gemma system prompt, generation size, progress mapping, upscale, Printify blueprint, card aspect ratio, UI copy) look up product data instead of branching on strings. Tees and posters share one queue, one library and one review flow.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest, vanilla JS/CSS front end, diffusers + torch (ROCm) for generation.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.12**; this repo's venv was built with `uv`. Windows interpreter is `.venv\Scripts\python`, and `pytest` is `.venv\Scripts\python -m pytest`.
- **Tee behaviour must not change.** Every existing design row stays a tee, every existing prompt path produces the same bytes, and `current_size()` keeps returning a plain `int`. Where a formula changes, it must be arithmetically identical for tees — the plan states the check.
- **Test style** follows `tests/test_pipeline.py`: plain pytest functions, `monkeypatch`, no fixture files, no test classes. `_FakePipe` / `_FakeImage` are existing helpers, not a new pattern.
- **Commit after every task.** The branch is `poster-size-probe`.
- `main.DEFAULT_PROMPT` (the idea prompt you copy into ChatGPT) stays tee-worded — explicitly out of scope.
- Do not add dependencies. Everything here uses what is already installed.
- Full test suite must pass at the end of every task: `.venv\Scripts\python -m pytest -q` (currently 113 passing).

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `db.py` | schema + migrations | Modify: one migration row |
| `pipeline.py` | prompts, sizes, product registry, generation | Modify: `POSTER_TEMPLATE`, `poster_size`, `PRODUCTS`, `product_data`, `product_size`, `build_prompt`, `step_progress`, `generate_image_local` |
| `refine.py` | Gemma system prompts | Modify: `DEFAULT_REFINE_PROMPT_POSTER`, `DEFAULTS` |
| `worker.py` | queue consumer | Modify: product-aware prompt, size, decode share |
| `main.py` | HTTP surface | Modify: bodies, validation, `/api/products`, settings, regenerate |
| `printify.py` | publishing | Modify: product-aware blueprint, variants, price, title |
| `upscale.py` | 4x print file | Modify: GPU device with CPU fallback |
| `static/index.html` | markup | Modify: product selects, settings fields, copy |
| `static/app.js` | client logic | Modify: card aspect, product plumbing, creep rate |
| `static/styles.css` | card shape | Modify: one poster aspect rule |
| `tests/test_printify.py` | publishing tests | **Create** |
| `tests/test_db.py`, `test_pipeline.py`, `test_worker.py`, `test_api.py`, `test_refine.py` | | Modify: new tests |

---

### Task 1: The `product` column

**Files:**
- Modify: `db.py:32-40` (the `MIGRATIONS` tuple)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces: `designs.product TEXT NOT NULL DEFAULT 'tee'` — every later task reads or writes this column.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_db.py`:

```python
def test_product_column_defaults_to_tee(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.init()  # run twice: must not raise
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
        assert "product" in cols
        # a row inserted by code that predates products must still land somewhere valid
        con.execute("INSERT INTO designs (phrase) VALUES ('dog dad')")
        row = con.execute("SELECT product FROM designs").fetchone()
    assert row["product"] == "tee"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_db.py::test_product_column_defaults_to_tee -v`
Expected: FAIL with `assert 'product' in cols`

- [ ] **Step 3: Write minimal implementation**

In `db.py`, append one entry to the `MIGRATIONS` tuple after `("progress", ...)`:

```python
    ("product", "ALTER TABLE designs ADD COLUMN product TEXT NOT NULL DEFAULT 'tee'"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_db.py -v`
Expected: all PASS, including the existing migration tests

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: add product column defaulting to tee"
```

---

### Task 2: Poster prompt templates

**Files:**
- Modify: `pipeline.py:3-7` (add `POSTER_TEMPLATE` after `PROMPT_TEMPLATE`)
- Modify: `refine.py:8-16` (add `DEFAULT_REFINE_PROMPT_POSTER` and `DEFAULTS`)
- Test: `tests/test_pipeline.py`, `tests/test_refine.py`

**Interfaces:**
- Consumes: nothing
- Produces: `pipeline.POSTER_TEMPLATE` (a `{phrase}`/`{style}` format string), `refine.DEFAULT_REFINE_PROMPT_POSTER` (a `{n}` format string), `refine.DEFAULTS: dict[str, str]` keyed by settings key. Task 4 references `POSTER_TEMPLATE`; Task 8 references `refine.DEFAULTS`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def test_poster_template_takes_the_same_placeholders_as_the_tee_one():
    filled = pipeline.POSTER_TEMPLATE.format(phrase="a lighthouse", style="Style: art deco. ")
    assert "a lighthouse" in filled and "art deco" in filled


def test_poster_template_says_what_it_wants_not_what_it_doesnt():
    # Z-Image-Turbo runs at guidance_scale=0.0, so with no classifier-free guidance
    # negative phrasing barely binds - the probe asked for "no frame, no border" and
    # got a prominent frame. This template must not repeat that mistake.
    assert "no " not in pipeline.POSTER_TEMPLATE.lower()


def test_poster_template_asks_for_one_subject_filling_a_tall_canvas():
    t = pipeline.POSTER_TEMPLATE.lower()
    assert "one clear focal subject" in t
    assert "edge to edge" in t
```

Append to `tests/test_refine.py`:

```python
import refine


def test_poster_refine_prompt_is_wall_art_not_garment():
    p = refine.DEFAULT_REFINE_PROMPT_POSTER.lower()
    assert "t-shirt" not in p and "shirt" not in p
    assert "5:7" in p


def test_refine_defaults_cover_every_settings_key():
    assert refine.DEFAULTS["refine_prompt"] is refine.DEFAULT_REFINE_PROMPT
    assert refine.DEFAULTS["refine_prompt_poster"] is refine.DEFAULT_REFINE_PROMPT_POSTER
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -k poster_template tests/test_refine.py -v`
Expected: FAIL with `AttributeError: module 'pipeline' has no attribute 'POSTER_TEMPLATE'`

- [ ] **Step 3: Write minimal implementation**

In `pipeline.py`, directly after `PROMPT_TEMPLATE`:

```python
# Unlike PROMPT_TEMPLATE this states what it wants rather than what it doesn't.
# Z-Image-Turbo runs at guidance_scale=0.0, so with no classifier-free guidance
# negative phrasing barely binds - the size probe's prompt said "no frame, no
# border" and the image came out with a prominent frame.
POSTER_TEMPLATE = (
    "Fine-art poster print: {phrase}. "
    "{style}One clear focal subject, upright vertical composition, "
    "artwork running edge to edge and filling the whole canvas, "
    "clear foreground and distant background."
)
```

In `refine.py`, directly after `DEFAULT_REFINE_PROMPT`:

```python
DEFAULT_REFINE_PROMPT_POSTER = (
    "You are an art director for a print-on-demand wall-art brand. Given a concept "
    "and optional style keywords, write {n} distinct, vivid image-generation prompts "
    "- each a different creative interpretation. Use the style keywords as creative "
    "direction. Every prompt must describe a single upright poster artwork with one "
    "clear focal subject, composed for a tall 5:7 canvas and running edge to edge. "
    "Output only the prompts, numbered 1 to {n}, one per line."
)

# Keyed by settings key so pipeline.PRODUCTS can name a prompt without importing
# this module, and main can resolve `saved value or default` with one lookup.
DEFAULTS = {
    "refine_prompt": DEFAULT_REFINE_PROMPT,
    "refine_prompt_poster": DEFAULT_REFINE_PROMPT_POSTER,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py tests/test_refine.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline.py refine.py tests/test_pipeline.py tests/test_refine.py
git commit -m "feat: add poster prompt templates phrased positively for zero-guidance"
```

---

### Task 3: `poster_size()` — the ladder rung setting

**Files:**
- Modify: `pipeline.py` (after `poster_dpi`, currently ending line 270)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `pipeline.POSTER_LADDER`, `pipeline.poster_dpi` (both already exist)
- Produces: `pipeline.DEFAULT_POSTER_SIZE: tuple[int, int]` = `(960, 1344)`, and `pipeline.poster_size() -> tuple[int, int]` reading the `poster_size` setting. Task 4 puts `poster_size` in the registry; Task 8 exposes it over HTTP.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def test_poster_size_defaults_to_the_measured_rung(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    assert pipeline.poster_size() == (960, 1344)


def test_poster_size_reads_a_saved_rung(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    db.set_setting("poster_size", "1120x1568")
    assert pipeline.poster_size() == (1120, 1568)


def test_poster_size_rejects_a_size_that_is_not_on_the_ladder(tmp_path, monkeypatch):
    # a hand-edited setting must not be able to ask the GPU for a size we never
    # tested - every rung is 16-aligned and exact 5:7, and an arbitrary one is not
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    db.set_setting("poster_size", "2000x3000")
    assert pipeline.poster_size() == pipeline.DEFAULT_POSTER_SIZE


def test_poster_size_survives_junk(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    for junk in ("", "big", "960", "960x", "axb", "960x1344x99"):
        db.set_setting("poster_size", junk)
        assert pipeline.poster_size() == pipeline.DEFAULT_POSTER_SIZE


def test_default_poster_size_is_actually_on_the_ladder():
    assert pipeline.DEFAULT_POSTER_SIZE in pipeline.POSTER_LADDER
```

`tests/test_pipeline.py` imports `pipeline` but not `db`. Add `import db` to its import block at the top.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -k poster_size -v`
Expected: FAIL with `AttributeError: module 'pipeline' has no attribute 'poster_size'`

- [ ] **Step 3: Write minimal implementation**

In `pipeline.py`, directly after `poster_dpi`:

```python
DEFAULT_POSTER_SIZE = (960, 1344)  # the only rung measured working end to end


def poster_size() -> tuple[int, int]:
    """Which ladder rung posters generate at, from the `poster_size` setting.

    Anything not literally on POSTER_LADDER falls back to the measured default:
    every rung is exact 5:7 and 16-aligned, and a hand-edited setting must not be
    able to ask the GPU for a shape we never tested. 19 minutes is a long time to
    wait to find out a size doesn't decode.
    """
    import db

    raw = db.get_setting("poster_size") or ""
    try:
        width, height = (int(n) for n in raw.lower().split("x"))
    except ValueError:
        return DEFAULT_POSTER_SIZE
    return (width, height) if (width, height) in POSTER_LADDER else DEFAULT_POSTER_SIZE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: read the poster ladder rung from a setting, rejecting untested sizes"
```

---

### Task 4: The `PRODUCTS` registry

**Files:**
- Modify: `pipeline.py` (after `poster_size` from Task 3)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `PROMPT_TEMPLATE`, `POSTER_TEMPLATE` (Task 2), `poster_size` (Task 3), `current_size`, `ZIMAGE_STEPS` — all defined above this point in the file.
- Produces:
  - `pipeline.PRODUCTS: dict[str, dict]` with keys `label`, `template`, `refine_key`, `size`, `aspect`, `decode_share`, `eta_minutes`, `blueprint_key`, `blueprint_default`, `price_cents`, `title_suffix`
  - `pipeline.DEFAULT_PRODUCT = "tee"`
  - `pipeline.product_data(name: str | None) -> dict`
  - `pipeline.product_size(name: str | None) -> tuple[int, int]`

**Placement matters:** the dict evaluates `POSTER_TEMPLATE`, `ZIMAGE_STEPS`, `current_size` and `poster_size` at import time, so it must sit below all of them.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
PRODUCT_KEYS = {"label", "template", "refine_key", "size", "aspect", "decode_share",
                "eta_minutes", "blueprint_key", "blueprint_default", "price_cents",
                "title_suffix"}


def test_every_product_carries_every_key():
    # a missing key is a KeyError 19 minutes into a generation run, so check the
    # shape of the registry rather than trusting each call site to be careful
    for name, data in pipeline.PRODUCTS.items():
        assert set(data) == PRODUCT_KEYS, name


def test_default_product_is_in_the_registry():
    assert pipeline.DEFAULT_PRODUCT in pipeline.PRODUCTS


def test_product_data_falls_back_like_current_model_does():
    assert pipeline.product_data("poster")["label"] == "Poster (50x70cm)"
    assert pipeline.product_data("hoodie") is pipeline.PRODUCTS["tee"]
    assert pipeline.product_data(None) is pipeline.PRODUCTS["tee"]
    assert pipeline.product_data("") is pipeline.PRODUCTS["tee"]


def test_tee_size_is_square_and_still_obeys_speed_production(monkeypatch):
    monkeypatch.setattr(pipeline, "current_size", lambda: 512)
    assert pipeline.product_size("tee") == (512, 512)
    monkeypatch.setattr(pipeline, "current_size", lambda: 1024)
    assert pipeline.product_size("tee") == (1024, 1024)


def test_poster_size_ignores_speed_production(monkeypatch):
    # a 512-tall poster is unprintable; Speed Production is a t-shirt sifting tool
    monkeypatch.setattr(pipeline, "current_size", lambda: 512)
    monkeypatch.setattr(pipeline, "poster_size", lambda: (960, 1344))
    assert pipeline.product_size("poster") == (960, 1344)


def test_unknown_product_generates_as_a_tee(monkeypatch):
    monkeypatch.setattr(pipeline, "current_size", lambda: 1024)
    assert pipeline.product_size("hoodie") == (1024, 1024)


def test_tee_decode_share_matches_the_old_reserved_fraction():
    assert pipeline.PRODUCTS["tee"]["decode_share"] == 1 / (pipeline.ZIMAGE_STEPS + 1)


def test_poster_aspect_is_the_5_7_the_ladder_generates():
    assert pipeline.PRODUCTS["poster"]["aspect"] == "5 / 7"
    assert pipeline.PRODUCTS["tee"]["aspect"] == "1"


def test_tee_blueprint_default_is_the_gildan_tee_it_always_was():
    assert pipeline.PRODUCTS["tee"]["blueprint_default"] == "6"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -k "product" -v`
Expected: FAIL with `AttributeError: module 'pipeline' has no attribute 'PRODUCTS'`

- [ ] **Step 3: Write minimal implementation**

In `pipeline.py`, after `poster_size`:

```python
# What a product IS, in one place. Same idea as the MODELS registry above: each
# seam looks its product up rather than branching on a string, so a third product
# is one dict entry instead of a hunt through six files.
#
# `size` is a callable so both products resolve the same way at call time - which
# is also what keeps Speed Production wired to tees only. Both entries call
# through a lambda rather than naming the function directly: a direct reference
# would bind the function object at import time, so monkeypatching
# pipeline.poster_size in a test would silently not take.
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
        "blueprint_default": "6",   # Unisex Heavy Cotton Tee (Gildan 5000)
        "price_cents": 2499,
        "title_suffix": "T-Shirt",
    },
    "poster": {
        "label": "Poster (50x70cm)",
        "template": POSTER_TEMPLATE,
        "refine_key": "refine_prompt_poster",
        "size": lambda: poster_size(),
        "aspect": "5 / 7",
        # measured: 960x1344 spends ~19 of 19.5 min in the VAE decode, because
        # MIOpen has no CK grouped-conv library for gfx1031
        "decode_share": 0.95,
        "eta_minutes": 20,
        "blueprint_key": "printify_poster_blueprint_id",
        "blueprint_default": "",    # unknown: the catalogue endpoint 401s on this token
        "price_cents": 3499,        # placeholder until the blueprint is chosen
        "title_suffix": "Poster",
    },
}
DEFAULT_PRODUCT = "tee"


def product_data(name: str | None) -> dict:
    """Registry entry for a product. An unrecognised name falls back to the
    default rather than raising - same habit as current_model(), and for the same
    reason: a bad database value should degrade, not kill the worker thread."""
    return PRODUCTS.get(name or "", PRODUCTS[DEFAULT_PRODUCT])


def product_size(name: str | None) -> tuple[int, int]:
    """The (width, height) this product generates at, resolved now."""
    return product_data(name)["size"]()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: add a PRODUCTS registry beside MODELS"
```

---

### Task 5: `build_prompt` becomes product-aware

**Files:**
- Modify: `pipeline.py:81-83`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `product_data` (Task 4)
- Produces: `build_prompt(phrase: str, filters: str, product: str = "tee") -> str`. The default keeps all existing call sites and tests passing unchanged. Task 7 (worker) passes the third argument.

**Placement matters:** `build_prompt` currently sits at line 81, above `PRODUCTS`. Since it only calls `product_data` at *call* time, not import time, it can stay where it is.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def test_build_prompt_defaults_to_the_tee_template():
    # every existing caller passes two arguments and must keep getting a tee
    assert build_prompt("dog dad", "") == build_prompt("dog dad", "", "tee")
    assert "t-shirt" in build_prompt("dog dad", "").lower()


def test_build_prompt_uses_the_poster_template_for_posters():
    p = build_prompt("a lighthouse at sunset", "art deco", "poster")
    assert "poster print" in p.lower()
    assert "t-shirt" not in p.lower()
    assert "a lighthouse at sunset" in p and "art deco" in p


def test_build_prompt_style_clause_works_for_both_products():
    for product in pipeline.PRODUCTS:
        assert "Style:" not in build_prompt("dog dad", "", product)
        assert "Style: vintage." in build_prompt("dog dad", "vintage", product)


def test_build_prompt_falls_back_to_tee_on_an_unknown_product():
    assert build_prompt("dog dad", "", "hoodie") == build_prompt("dog dad", "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -k build_prompt -v`
Expected: FAIL with `TypeError: build_prompt() takes 2 positional arguments but 3 were given`

- [ ] **Step 3: Write minimal implementation**

Replace `build_prompt` in `pipeline.py`:

```python
def build_prompt(phrase: str, filters: str, product: str = "tee") -> str:
    """Wrap a phrase in its product's template. The default keeps every caller
    that predates posters producing exactly the prompt it produced before."""
    style = f"Style: {filters}. " if filters else ""
    return product_data(product)["template"].format(phrase=phrase, style=style)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -q`
Expected: all PASS, including the pre-existing `test_prompt_includes_phrase_and_filters`

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: build_prompt picks its template from the product"
```

---

### Task 6: Progress that tells the truth on a poster

**Files:**
- Modify: `pipeline.py:273-276` (`step_progress`) and `pipeline.py:283-316` (`generate_image_local`)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `ZIMAGE_STEPS`
- Produces:
  - `step_progress(step_index: int, steps: int, decode_share: float = 1 / (ZIMAGE_STEPS + 1)) -> int`
  - `generate_image_local(prompt, on_step=None, size=None, decode_share=None) -> bytes`

Why `decode_share` is a parameter on `generate_image_local` rather than looked up inside: the callback fires deep in the diffusers loop, and the function takes a `size`, not a product. Keeping both explicit means `probe_poster.py` keeps working with no change and there is exactly one way to say each thing.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`:

```python
def test_step_progress_is_unchanged_for_tees():
    # the old formula was round((i + 1) / (steps + 1) * 100); the new one must be
    # arithmetically identical at the tee's decode share, not merely close
    steps = pipeline.ZIMAGE_STEPS
    for i in range(steps):
        assert step_progress(i, steps) == round((i + 1) / (steps + 1) * 100)


def test_step_progress_still_reserves_the_top_of_the_bar():
    assert step_progress(pipeline.ZIMAGE_STEPS - 1, pipeline.ZIMAGE_STEPS) == 90


def test_step_progress_gives_a_poster_loop_only_its_real_share():
    # a poster spends ~19 of 19.5 minutes decoding: the loop must not claim 90%
    # of the bar in five seconds and then sit there
    steps = pipeline.ZIMAGE_STEPS
    assert step_progress(steps - 1, steps, decode_share=0.95) == 5
    assert step_progress(0, steps, decode_share=0.95) == 1


def test_generate_passes_the_decode_share_through_to_the_callback(monkeypatch):
    fake = _fake_loaded_pipe(monkeypatch)
    seen = []
    pipeline.generate_image_local("a poster", on_step=seen.append,
                                  size=(960, 1344), decode_share=0.95)
    fake.kwargs["callback_on_step_end"](None, pipeline.ZIMAGE_STEPS - 1, None, {})
    assert seen == [5]


def test_generate_without_a_decode_share_reports_like_a_tee(monkeypatch):
    fake = _fake_loaded_pipe(monkeypatch)
    seen = []
    pipeline.generate_image_local("a tee", on_step=seen.append)
    fake.kwargs["callback_on_step_end"](None, pipeline.ZIMAGE_STEPS - 1, None, {})
    assert seen == [90]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -k "step_progress or decode_share" -v`
Expected: FAIL with `TypeError: step_progress() got an unexpected keyword argument 'decode_share'`

- [ ] **Step 3: Write minimal implementation**

Replace `step_progress` in `pipeline.py`:

```python
def step_progress(step_index: int, steps: int,
                  decode_share: float = 1 / (ZIMAGE_STEPS + 1)) -> int:
    """Percent to show after finishing step `step_index` (0-based) of `steps`.

    `decode_share` is the fraction of the bar reserved for the VAE decode that
    follows the loop. At the tee default this is arithmetically identical to the
    old round((i + 1) / (steps + 1) * 100): with 9 steps and a 0.1 share, step 0
    gives 10 and step 8 gives 90, exactly as before.

    A poster is the reason this is a parameter. Its decode is ~19 of 19.5 minutes
    (MIOpen has no CK grouped-conv library for gfx1031), so a loop that claimed
    90% of the bar in five seconds would read as a hung job for the next nineteen.
    """
    return round((step_index + 1) / steps * (1 - decode_share) * 100)
```

In `generate_image_local`, change the signature and the callback:

```python
def generate_image_local(prompt: str, on_step=None, size: tuple[int, int] | None = None,
                         decode_share: float | None = None) -> bytes:
    """Generate one PNG on the local GPU (needs requirements-local.txt).
    Uses whichever model `image_model` names; on_step(pct) gets an int 0-100.
    `size` is an explicit (width, height) - posters need a non-square one and must
    not be shrunk by Speed Production. Omit it for the square t-shirt default.
    `decode_share` is how much of the progress bar to leave for the VAE decode;
    omit it for the tee default."""
```

and inside, replace the `_cb` body:

```python
    share = decode_share if decode_share is not None else 1 / (ZIMAGE_STEPS + 1)

    def _cb(pipe, step_index, timestep, kwargs):
        if on_step:
            on_step(step_progress(step_index, steps, share))
        return kwargs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_pipeline.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline.py tests/test_pipeline.py
git commit -m "feat: let the progress bar reserve a poster's real decode share"
```

---

### Task 7: The worker generates the right product

**Files:**
- Modify: `worker.py:27-36`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `pipeline.DEFAULT_PRODUCT`, `pipeline.build_prompt(phrase, filters, product)`, `pipeline.product_size(product)`, `pipeline.product_data(product)["decode_share"]`
- Produces: nothing new — this is the wiring task.

**Watch out:** every existing fake in `tests/test_worker.py` is `lambda prompt, on_step=None: ...`. Once the worker passes `size=` and `decode_share=`, those fakes raise `TypeError`. Step 3 updates them; that is expected churn, not breakage.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_worker.py`:

```python
def queue_poster():
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, product) "
                    "VALUES ('a lighthouse', '', 'queued', 'poster')")


def test_poster_generates_at_the_poster_size(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}

    def fake(prompt, on_step=None, size=None, decode_share=None):
        seen.update(prompt=prompt, size=size, decode_share=decode_share)
        return b"fake-png"

    monkeypatch.setattr(worker.pipeline, "generate_image_local", fake)
    monkeypatch.setattr(worker.pipeline, "poster_size", lambda: (960, 1344))
    queue_poster()
    assert worker.process_next() is True
    assert seen["size"] == (960, 1344)
    assert seen["decode_share"] == 0.95
    assert "poster print" in seen["prompt"].lower()


def test_tee_still_generates_square_at_the_current_size(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}

    def fake(prompt, on_step=None, size=None, decode_share=None):
        seen.update(prompt=prompt, size=size)
        return b"fake-png"

    monkeypatch.setattr(worker.pipeline, "generate_image_local", fake)
    monkeypatch.setattr(worker.pipeline, "current_size", lambda: 1024)
    queue_one()
    assert worker.process_next() is True
    assert seen["size"] == (1024, 1024)
    assert "t-shirt" in seen["prompt"].lower()


def test_unknown_product_generates_as_a_tee(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}
    monkeypatch.setattr(worker.pipeline, "generate_image_local",
                        lambda p, on_step=None, size=None, decode_share=None:
                        seen.update(size=size) or b"PNG")
    monkeypatch.setattr(worker.pipeline, "current_size", lambda: 1024)
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, product) "
                    "VALUES ('dog dad', '', 'queued', 'hoodie')")
    assert worker.process_next() is True
    assert seen["size"] == (1024, 1024)


def test_test_row_still_uses_its_raw_prompt_whatever_the_product(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}
    monkeypatch.setattr(worker.pipeline, "generate_image_local",
                        lambda p, on_step=None, size=None, decode_share=None:
                        seen.update(prompt=p, size=size) or b"PNG")
    monkeypatch.setattr(worker.pipeline, "poster_size", lambda: (960, 1344))
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, test, product) "
                    "VALUES ('a red dragon', '', 'queued', 1, 'poster')")
    assert worker.process_next() is True
    assert seen["prompt"] == "a red dragon"   # no template, either product's
    assert seen["size"] == (960, 1344)        # but still the poster's shape
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_worker.py -v`
Expected: FAIL — the new tests see `size=None` because the worker does not pass it yet

- [ ] **Step 3: Write minimal implementation**

In `worker.py`, replace the body of the `try:` block's prompt and generate lines:

```python
    try:
        # A refined Gemma prompt (or a raw Test-tab prompt) is used verbatim;
        # otherwise wrap the phrase in the product's template.
        product = row["product"] or pipeline.DEFAULT_PRODUCT
        prompt = row["prompt"] or (
            row["phrase"] if row["test"]
            else pipeline.build_prompt(row["phrase"], row["filters"], product)
        )

        def on_step(pct):
            with db.connect() as con:
                con.execute("UPDATE designs SET progress = ? WHERE id = ?", (pct, row["id"]))

        png = pipeline.generate_image_local(
            prompt, on_step=on_step,
            size=pipeline.product_size(product),
            decode_share=pipeline.product_data(product)["decode_share"],
        )
```

Then update the five existing fakes in `tests/test_worker.py` so their signatures accept the new keywords. For example `test_generates_writes_file`:

```python
    monkeypatch.setattr(worker.pipeline, "generate_image_local",
                        lambda prompt, on_step=None, size=None, decode_share=None: b"fake-png")
```

Apply the same `, size=None, decode_share=None` addition to the fakes in
`test_reports_progress_via_callback`, `test_failure_marks_failed_with_error`, and
`test_uses_stored_prompt_verbatim`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_worker.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add worker.py tests/test_worker.py
git commit -m "feat: worker generates each design as the product it is"
```

---

### Task 8: The HTTP surface

**Files:**
- Modify: `main.py:63-89` (bodies), `92-121` (generate), `124-137` (test), `140-142` (near `/api/styles`), `230-244` (regenerate), `287-305` (settings)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `pipeline.PRODUCTS`, `pipeline.DEFAULT_PRODUCT`, `pipeline.product_data`, `pipeline.POSTER_LADDER`, `pipeline.poster_dpi`, `pipeline.poster_size`, `refine.DEFAULTS`
- Produces:
  - `GET /api/products` -> `{name: {label, aspect, eta_minutes}}`
  - `GenerateBody.product: str = "tee"`, `TestBody.product: str = "tee"`
  - `SettingsBody.poster_size`, `.printify_poster_blueprint_id`, `.refine_prompt_poster`
  - `get_settings()` gains `poster_size` (string), `poster_sizes` (list of `{value, label}`), `printify_poster_blueprint_id` (bool), `refine_prompt_poster` (string)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`:

```python
import pipeline


def test_products_endpoint_lists_both_with_what_the_ui_needs(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    out = main.list_products()
    assert set(out) == set(pipeline.PRODUCTS)
    assert out["poster"]["aspect"] == "5 / 7"
    assert out["poster"]["eta_minutes"] == 20
    assert out["tee"]["label"] == "T-shirt"


def test_generate_records_the_product_on_every_row(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    main.generate(main.GenerateBody(text="a lighthouse", variations=2,
                                    refine=False, product="poster"))
    with db.connect() as con:
        rows = con.execute("SELECT product FROM designs").fetchall()
    assert [r["product"] for r in rows] == ["poster", "poster"]


def test_generate_defaults_to_tee(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    main.generate(main.GenerateBody(text="dog dad", variations=1, refine=False))
    with db.connect() as con:
        assert con.execute("SELECT product FROM designs").fetchone()["product"] == "tee"


def test_generate_rejects_an_unknown_product(tmp_path, monkeypatch):
    # the request body is the one place a bad product should be loud: silently
    # coercing it would queue 20 minutes of the wrong thing
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.generate(main.GenerateBody(text="dog dad", refine=False, product="hoodie"))
    assert e.value.status_code == 400


def test_test_endpoint_records_the_product(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    main.generate_test(main.TestBody(text="a red dragon", product="poster"))
    with db.connect() as con:
        assert con.execute("SELECT product FROM designs").fetchone()["product"] == "poster"


def test_test_endpoint_rejects_an_unknown_product(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.generate_test(main.TestBody(text="x", product="hoodie"))
    assert e.value.status_code == 400


def test_regenerate_carries_the_product_forward(tmp_path, monkeypatch):
    # without this a regenerated poster silently becomes a tee - and you find out
    # when a 5:7 image comes back square
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending", product="poster")
    main.regenerate(did)
    with db.connect() as con:
        rows = con.execute("SELECT product FROM designs ORDER BY id").fetchall()
    assert [r["product"] for r in rows] == ["poster", "poster"]


def test_settings_roundtrips_poster_size(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["poster_size"] == "960x1344"
    main.save_settings(main.SettingsBody(poster_size="1120x1568"))
    assert main.get_settings()["poster_size"] == "1120x1568"


def test_settings_offers_every_ladder_rung_with_its_dpi(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    sizes = main.get_settings()["poster_sizes"]
    assert len(sizes) == len(pipeline.POSTER_LADDER)
    assert sizes[0]["value"] == "1440x2016"
    assert "292 dpi" in sizes[0]["label"]


def test_settings_roundtrips_the_poster_blueprint(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["printify_poster_blueprint_id"] is False
    main.save_settings(main.SettingsBody(printify_poster_blueprint_id="1220"))
    assert main.get_settings()["printify_poster_blueprint_id"] is True


def test_settings_roundtrips_the_poster_refine_prompt(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    import refine
    assert main.get_settings()["refine_prompt_poster"] == refine.DEFAULT_REFINE_PROMPT_POSTER
    main.save_settings(main.SettingsBody(refine_prompt_poster="my poster prompt"))
    assert main.get_settings()["refine_prompt_poster"] == "my poster prompt"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_api.py -v`
Expected: FAIL with `AttributeError: module 'main' has no attribute 'list_products'`

- [ ] **Step 3: Write minimal implementation**

In `main.py`, add `product` to the two bodies and the three settings keys:

```python
class GenerateBody(BaseModel):
    text: str
    variations: int = 2
    style: str = ""
    refine: bool = True
    product: str = "tee"


class TestBody(BaseModel):
    text: str
    product: str = "tee"
```

and inside `SettingsBody`, after `speed_production`:

```python
    poster_size: str = ""
    printify_poster_blueprint_id: str = ""
    refine_prompt_poster: str = ""
```

Add a validator helper above `generate`:

```python
def _product(name: str) -> str:
    """A product from a request body. Unlike a database value this is loud on
    nonsense - queueing 20 minutes of the wrong shape is worse than a 400."""
    if name not in pipeline.PRODUCTS:
        raise HTTPException(400, "Unknown product: %s" % name)
    return name
```

In `generate`, take the product first and thread it through:

```python
@app.post("/api/generate")
def generate(body: GenerateBody, _gate: None = Depends(require_access_code)):
    product = _product(body.product)
    items = pipeline.parse_input(body.text)
    if not items:
        raise HTTPException(400, "No valid lines found")
    if _queue_full():
        raise HTTPException(429, "Queue is full - try again shortly")
    refine_key = pipeline.product_data(product)["refine_key"]
    system_prompt = db.get_setting(refine_key) or refine.DEFAULTS[refine_key]
```

and the insert:

```python
    with db.connect() as con:
        con.executemany(
            "INSERT INTO designs (phrase, filters, prompt, product, status) "
            "VALUES (?, ?, ?, ?, 'queued')",
            [(phrase, filters, prompt, product) for phrase, filters, prompt in rows],
        )
```

In `generate_test`:

```python
    product = _product(body.product)
    ...
        cur = con.execute(
            "INSERT INTO designs (phrase, filters, status, test, product) "
            "VALUES (?, '', 'queued', 1, ?)",
            (text, product),
        )
```

Add the endpoint next to `/api/styles`:

```python
@app.get("/api/products")
def list_products():
    """What the front end needs to draw a product: its name, its card shape, and
    how long to expect a generation to take."""
    return {
        name: {"label": d["label"], "aspect": d["aspect"], "eta_minutes": d["eta_minutes"]}
        for name, d in pipeline.PRODUCTS.items()
    }
```

In `regenerate`, select and re-insert the product:

```python
        row = con.execute(
            "SELECT phrase, filters, product FROM designs WHERE id = ?", (design_id,)
        ).fetchone()
        ...
        con.execute(
            "INSERT INTO designs (phrase, filters, product, status) VALUES (?, ?, ?, 'queued')",
            (row["phrase"], row["filters"], row["product"]),
        )
```

In `get_settings`, add to the returned dict:

```python
    out["printify_poster_blueprint_id"] = bool(db.get_setting("printify_poster_blueprint_id"))
    out["refine_prompt_poster"] = (
        db.get_setting("refine_prompt_poster") or refine.DEFAULT_REFINE_PROMPT_POSTER
    )
    out["poster_size"] = "%dx%d" % pipeline.poster_size()
    out["poster_sizes"] = [
        {"value": "%dx%d" % (w, h),
         "label": "%dx%d — %d dpi" % (w, h, pipeline.poster_dpi(w, h))}
        for w, h in pipeline.POSTER_LADDER
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_api.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_api.py
git commit -m "feat: accept and record a product on every queued design"
```

---

### Task 9: Product-aware publishing

**Files:**
- Modify: `printify.py` (whole file)
- Modify: `main.py:255-284` (`publish`)
- Test: `tests/test_printify.py` (**create**)

**Interfaces:**
- Consumes: `pipeline.product_data`, `pipeline.DEFAULT_PRODUCT`
- Produces: `printify.POSTER_VARIANT_MATCH: tuple[str, ...]`, `printify._select_variants(product: str, variants: list) -> list`, `printify._blueprint(data: dict) -> int`, and `printify.publish(design: dict) -> str` reading `design["product"]`.

**Reality check for the implementer:** the saved token 401s on poster variant listings, so the poster variant naming is *unverified*. `POSTER_VARIANT_MATCH` is deliberately a list of plausible spellings with a fallback, and it is the single place to correct once a token with shop scope exists.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_printify.py`:

```python
"""Publishing tests with requests mocked - no Printify account needed."""
import pytest

import db
import pipeline
import printify


def setup_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    db.set_setting("printify_api_token", "tok")
    db.set_setting("printify_shop_id", "99")


TEE_VARIANTS = [
    {"id": 1, "options": {"color": "Black", "size": "L"}},
    {"id": 2, "options": {"color": "Neon Pink", "size": "L"}},
    {"id": 3, "options": {"color": "White", "size": "M"}},
]
POSTER_VARIANTS = [
    {"id": 10, "options": {"size": "30x40 cm"}},
    {"id": 11, "options": {"size": "50x70 cm"}},
    {"id": 12, "options": {"size": "70x100 cm"}},
]


def test_tee_variants_are_filtered_to_the_stocked_colours():
    assert [v["id"] for v in printify._select_variants("tee", TEE_VARIANTS)] == [1, 3]


def test_poster_variants_are_filtered_to_the_50x70():
    assert [v["id"] for v in printify._select_variants("poster", POSTER_VARIANTS)] == [11]


def test_poster_variant_match_survives_spacing():
    spaced = [{"id": 20, "options": {"size": "50 x 70 cm"}}]
    assert [v["id"] for v in printify._select_variants("poster", spaced)] == [20]


def test_unrecognised_catalogue_falls_back_rather_than_publishing_nothing():
    # better narrow than not at all - the same habit the tee path already had
    odd = [{"id": 30, "options": {"size": "A2"}}, {"id": 31, "options": {"size": "A1"}}]
    assert [v["id"] for v in printify._select_variants("poster", odd)] == [30, 31]
    assert printify._select_variants("tee", [{"id": 40, "options": {"color": "Lime"}}])


def test_tee_blueprint_defaults_to_the_gildan_when_unset(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert printify._blueprint(pipeline.PRODUCTS["tee"]) == 6


def test_tee_blueprint_can_be_overridden_by_a_setting(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("printify_blueprint_id", "12")
    assert printify._blueprint(pipeline.PRODUCTS["tee"]) == 12


def test_poster_blueprint_raises_when_not_configured(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match="blueprint"):
        printify._blueprint(pipeline.PRODUCTS["poster"])


def test_poster_blueprint_reads_its_own_setting(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("printify_poster_blueprint_id", "1220")
    assert printify._blueprint(pipeline.PRODUCTS["poster"]) == 1220


def _fake_api(monkeypatch, calls, variants):
    """Stand in for every Printify HTTP call, recording what was sent."""
    monkeypatch.setattr(printify, "_get", lambda path: (
        [{"id": 77, "title": "Some Provider"}] if "print_providers.json" in path
        else {"variants": variants}))

    def post(path, payload, timeout=60):
        calls.append((path, payload))
        if "uploads" in path:
            return {"id": "img-1"}
        return {"id": "prod-1"}

    monkeypatch.setattr(printify, "_post", post)


def test_publish_titles_and_prices_a_poster_as_a_poster(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("printify_poster_blueprint_id", "1220")
    src = tmp_path / "art.png"
    src.write_bytes(b"PNG")
    calls = []
    _fake_api(monkeypatch, calls, POSTER_VARIANTS)
    printify.publish({"id": 1, "phrase": "a lighthouse", "product": "poster",
                      "file": str(src), "print_file": None})
    product_call = [p for path, p in calls if path.endswith("/products.json")][0]
    assert product_call["title"] == "A Lighthouse Poster"
    assert product_call["blueprint_id"] == 1220
    assert product_call["variants"] == [{"id": 11, "price": 3499, "is_enabled": True}]


def test_publish_still_treats_a_design_with_no_product_as_a_tee(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    src = tmp_path / "art.png"
    src.write_bytes(b"PNG")
    calls = []
    _fake_api(monkeypatch, calls, TEE_VARIANTS)
    printify.publish({"id": 1, "phrase": "dog dad", "product": None,
                      "file": str(src), "print_file": None})
    product_call = [p for path, p in calls if path.endswith("/products.json")][0]
    assert product_call["title"] == "Dog Dad T-Shirt"
    assert product_call["blueprint_id"] == 6
    assert [v["price"] for v in product_call["variants"]] == [2499, 2499]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests/test_printify.py -v`
Expected: FAIL with `AttributeError: module 'printify' has no attribute '_select_variants'`

- [ ] **Step 3: Write minimal implementation**

In `printify.py`, replace the constants block and `publish`:

```python
import base64

import requests

import db
import pipeline

API = "https://api.printify.com/v1"
COLORS = {"Black", "White"}
# The 50x70cm variant's real name is unverified: this account's token 401s on the
# poster catalogue, so these are plausible spellings rather than a fact. Once a
# token with shop scope exists, correct this tuple - it is the only place to look.
# Haystacks are lower-cased with spaces stripped before matching, so "50 x 70 cm"
# matches "50x70" without needing its own entry.
POSTER_VARIANT_MATCH = ("50x70", "19.7x27.6")


def _blueprint(data: dict) -> int:
    """Which Printify product this design becomes."""
    raw = db.get_setting(data["blueprint_key"]) or data["blueprint_default"]
    if not raw:
        raise RuntimeError(
            "No Printify blueprint configured for %s - set it in settings" % data["label"]
        )
    return int(raw)


def _select_variants(product: str, variants: list) -> list:
    """The sellable variants for this product. Both paths keep the existing
    'or the first ten' fallback: against an unfamiliar catalogue, publishing
    narrow beats publishing nothing."""
    if product == "poster":
        for needle in POSTER_VARIANT_MATCH:
            hit = [v for v in variants
                   if needle in str(v["options"].get("size", "")).replace(" ", "").lower()]
            if hit:
                return hit
        return variants[:10]
    return [v for v in variants if v["options"].get("color") in COLORS] or variants[:10]
```

Then rewrite `publish` (the `_headers`, `_get`, `_post` helpers are unchanged):

```python
def publish(design: dict) -> str:
    shop_id = db.get_setting("printify_shop_id")
    product = design.get("product") or pipeline.DEFAULT_PRODUCT
    data = pipeline.product_data(product)
    blueprint_id = _blueprint(data)
    file_path = design.get("print_file") or design["file"]

    with open(file_path, "rb") as f:
        contents = base64.b64encode(f.read()).decode()
    image_id = _post(
        "/uploads/images.json",
        {"file_name": "design-%s.png" % design["id"], "contents": contents},
        timeout=120,
    )["id"]

    providers = _get("/catalog/blueprints/%d/print_providers.json" % blueprint_id)
    if not providers:
        raise RuntimeError("No print providers for blueprint %d" % blueprint_id)
    pp_id = providers[0]["id"]

    all_variants = _get(
        "/catalog/blueprints/%d/print_providers/%d/variants.json" % (blueprint_id, pp_id)
    )["variants"]
    variants = _select_variants(product, all_variants)

    product_json = _post(
        "/shops/%s/products.json" % shop_id,
        {
            "title": design["phrase"].title() + " " + data["title_suffix"],
            "description": design["phrase"],
            "blueprint_id": blueprint_id,
            "print_provider_id": pp_id,
            "variants": [
                {"id": v["id"], "price": data["price_cents"], "is_enabled": True}
                for v in variants
            ],
            "print_areas": [
                {
                    "variant_ids": [v["id"] for v in variants],
                    "placeholders": [
                        {
                            "position": "front",
                            "images": [
                                {"id": image_id, "x": 0.5, "y": 0.5, "scale": 1.0, "angle": 0}
                            ],
                        }
                    ],
                }
            ],
        },
    )
    product_id = product_json["id"]

    _post(
        "/shops/%s/products/%s/publish.json" % (shop_id, product_id),
        {"title": True, "description": True, "images": True, "variants": True, "tags": True},
    )
    return product_id
```

Delete the now-unused module constants `BLUEPRINT_ID` and `PRICE_CENTS` — both moved into the registry in Task 4.

In `main.publish`, add the blueprint guard after the existing Printify-configured check:

```python
    if not (db.get_setting("printify_api_token") and db.get_setting("printify_shop_id")):
        raise HTTPException(400, "Printify not configured - add your token and shop ID in settings")
    with db.connect() as con:
        row = con.execute(
            "SELECT * FROM designs WHERE id = ? AND status = 'approved'", (design_id,)
        ).fetchone()
    if not row:
        raise HTTPException(409, "Design must be approved first")
    data = pipeline.product_data(row["product"])
    if not (db.get_setting(data["blueprint_key"]) or data["blueprint_default"]):
        raise HTTPException(400, "No Printify blueprint configured for %s - "
                                 "add one in settings" % data["label"])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_printify.py tests/test_api.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add printify.py main.py tests/test_printify.py
git commit -m "feat: publish each design to its own product's blueprint"
```

---

### Task 10: Upscale on the GPU

**Files:**
- Modify: `upscale.py:7-23` and the `job()` body
- Test: `tests/test_upscale.py` (**create**)

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces: `upscale._device() -> torch.device`, `upscale._get_model(device)`. `upscale.upscale(design_id, src_path)` keeps its signature.

**Context:** `upscale.py:19` is `torch.device("mps" if ... else "cpu")` — there is no CUDA/ROCm branch, so every upscale on this machine runs on CPU. Pre-existing, but a poster is 1.7x the pixels of a tee and produces 3840x5376, which is what makes it hurt. ROCm reports as `cuda` in torch, so one branch covers the RX 6700.

- [ ] **Step 1: Write the failing test**

Create `tests/test_upscale.py`:

```python
import pytest

import upscale

torch = pytest.importorskip("torch")


def test_device_prefers_the_gpu(monkeypatch):
    # ROCm reports as cuda in torch, so this one branch covers the RX 6700
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert upscale._device().type == "cuda"


def test_device_falls_back_to_mps_then_cpu(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    assert upscale._device().type == "mps"
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    assert upscale._device().type == "cpu"


def test_models_are_cached_per_device(monkeypatch):
    # two devices must not share one loaded model, and asking twice for the same
    # device must not reload 60MB of weights
    made = []

    def fake_build(device):
        made.append(device.type)
        return object()

    monkeypatch.setattr(upscale, "_build_model", fake_build)
    monkeypatch.setattr(upscale, "_models", {})
    a = upscale._get_model(torch.device("cpu"))
    b = upscale._get_model(torch.device("cpu"))
    assert a is b and made == ["cpu"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python -m pytest tests/test_upscale.py -v`
Expected: FAIL with `AttributeError: module 'upscale' has no attribute '_device'`

- [ ] **Step 3: Write minimal implementation**

Replace the model section of `upscale.py`:

```python
_models = {}          # device type -> loaded RealESRGAN
_lock = threading.Lock()  # ponytail: one upscale at a time on an 8GB machine

WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "RealESRGAN_x4.pth")


def _device():
    """Best available device. ROCm reports as cuda in torch, so the first branch
    covers the RX 6700 - without it every upscale runs on CPU, which is tolerable
    at 1024px and painful at a poster's 960x1344."""
    import torch

    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _build_model(device):
    from py_real_esrgan.model import RealESRGAN

    model = RealESRGAN(device, scale=4)
    model.load_weights(WEIGHTS, download=True)
    return model


def _get_model(device):
    """Cached per device: the OOM path below reloads on CPU, and that must not
    evict the GPU model for the next design."""
    if device.type not in _models:
        _models[device.type] = _build_model(device)
    return _models[device.type]
```

and replace the `job()` body:

```python
    def job():
        with _lock:
            try:
                import torch
                from PIL import Image

                img = Image.open(src_path).convert("RGB")
                try:
                    result = _get_model(_device()).predict(img)
                except torch.OutOfMemoryError:
                    # a poster is 1.7x a tee's pixels and 4x output is 3840x5376;
                    # slow on CPU beats no print file at all
                    result = _get_model(torch.device("cpu")).predict(img)
                out_path = os.path.splitext(src_path)[0] + "_print.png"
                result.save(out_path)
                rel = os.path.join("designs", os.path.basename(out_path))
                with db.connect() as con:
                    con.execute(
                        "UPDATE designs SET print_file = ?, error = NULL WHERE id = ?", (rel, design_id)
                    )
            except Exception as e:
                # design stays approved; publish falls back to the original
                with db.connect() as con:
                    con.execute(
                        "UPDATE designs SET error = ? WHERE id = ?",
                        (("upscale failed: %s" % e)[:500], design_id),
                    )
```

Remove the now-unused `_model` global and the old `_get_model()` with no arguments.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests/test_upscale.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add upscale.py tests/test_upscale.py
git commit -m "fix: upscale on the GPU, falling back to CPU on OOM"
```

---

### Task 11: The front end

**Files:**
- Modify: `static/styles.css:186-192`
- Modify: `static/index.html:47, 88-101, 144-155, 205-213, 167-179`
- Modify: `static/app.js:206-230` (`queueItems`), `278-306` (settings savers), `404-441` (`creepTick`, `card`), `639-650` (`generateTest`), `803-870` (`loadPrompt`, `loadStyles`)
- Test: manual — no JS test harness exists in this repo

**Interfaces:**
- Consumes: `GET /api/products`, `GET/POST /api/settings` (Task 8), `designs[].product`
- Produces: nothing consumed by later tasks

- [ ] **Step 1: Card shape**

In `static/styles.css`, directly after the existing `.placeholder { ... }` block:

```css
/* Posters are the ladder's exact 5:7; the tee rule above is untouched. */
.card[data-product="poster"] img,
.card[data-product="poster"] .placeholder { aspect-ratio: 5 / 7; }
```

In `static/app.js`, in `card()`, add the attribute to the wrapper div:

```javascript
  return `<div class="card${selected.has(d.id) ? " selected" : ""}" data-id="${d.id}" data-product="${d.product || "tee"}"><div class="frame">${pick}${img}</div><div class="body"><div class="phrase">${esc(d.phrase)}</div>` +
```

Do the same in `libCard()` and `testCard()` so the Library and Test grids match.

- [ ] **Step 2: The product selector**

In `static/index.html`, inside the "Commission ideas" panel, add a row above the Art style row:

```html
        <div class="row">
          <label for="product_select" style="min-width:0">Product</label>
          <select id="product_select" onchange="productChanged()"></select>
          <span class="hint" id="product_state"></span>
        </div>
```

and in the Test panel, above the Generate button row:

```html
        <div class="row">
          <label for="test_product_select" style="min-width:0">Product</label>
          <select id="test_product_select"></select>
        </div>
```

In `static/app.js`, add the loader and send the value:

```javascript
let products = {};
async function loadProducts() {
  try {
    products = await api("/api/products");
    const opts = Object.entries(products).map(([name, p]) =>
      `<option value="${name}">${p.label}</option>`).join("");
    document.getElementById("product_select").innerHTML = opts;
    document.getElementById("test_product_select").innerHTML = opts;
    productChanged();
  } catch (e) {}
}
loadProducts();
```

In `queueItems`, read it and include it in the body:

```javascript
  const product = document.getElementById("product_select").value;
  const res = await api("/api/generate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text, style, refine, product})});
```

In `generateTest`, the same:

```javascript
    const product = document.getElementById("test_product_select").value;
    await api("/api/test", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text, product})});
```

- [ ] **Step 3: The refine box follows the product**

In `static/app.js`, replace the single `refine_box` wiring with product-keyed state:

```javascript
// One textarea, two system prompts. It always saves to the product it was
// written for, so switching the dropdown can never move your edits onto the
// other product.
let refinePrompts = {}, refineProduct = "tee";
function productChanged() {
  const name = document.getElementById("product_select").value || "tee";
  refineProduct = name;
  document.getElementById("refine_box").value = refinePrompts[name] || "";
  const p = products[name] || {};
  document.getElementById("product_state").textContent =
    p.eta_minutes ? `about ${p.eta_minutes} min per image on this GPU` : "";
}
```

In `loadPrompt`, populate both:

```javascript
    refinePrompts = {tee: s.refine_prompt || "", poster: s.refine_prompt_poster || ""};
    productChanged();
```

and replace the `refine_box` input listener body:

```javascript
    const key = refineProduct === "poster" ? "refine_prompt_poster" : "refine_prompt";
    refinePrompts[refineProduct] = document.getElementById("refine_box").value;
    try {
      await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({[key]: refinePrompts[refineProduct]})});
    } catch (e) { flash("Couldn't save the system prompt — " + e.message); }
```

Delete the old `document.getElementById("refine_box").value = s.refine_prompt || "";` line from `loadPrompt`.

- [ ] **Step 4: A progress bar that keeps moving through a poster's decode**

In `static/app.js`, replace `creepTick`:

```javascript
// How far ahead of the last reported step the bar is allowed to drift, and how
// fast. A tee reports a step every few seconds, so 18% of lead at 0.4/tick covers
// the gaps - those are the numbers this has always used. A poster's steps all
// land in the first five seconds and then nothing reports for ~19 minutes, so its
// lead has to span the rest of the bar, slowly enough to outlast the decode
// (0.008 per 120ms tick crosses 91% in about 23 minutes).
const CREEP = {tee: {lead: 18, perTick: 0.4}, poster: {lead: 91, perTick: 0.008}};
function creepTick() {
  const active = designs.find(d => d.status === "generating");
  if (!active) { creepId = null; creepVal = 0; return; }
  if (active.id !== creepId) { creepId = active.id; creepVal = active.progress || 0; }
  const c = CREEP[active.product] || CREEP.tee;
  const target = Math.min((active.progress || 0) + c.lead, 96);
  creepVal = Math.max(creepVal, Math.min(target, creepVal + c.perTick));
  const bar = document.querySelector(`.bar[data-id="${creepId}"]`);
  if (bar) bar.style.width = Math.max(2, creepVal) + "%";
}
```

In `card()`, tell the operator what the wait is:

```javascript
  const eta = (products[d.product || "tee"] || {}).eta_minutes;
  const working = generating
    ? "in press…" + (d.status === "generating" ? progressBar(d) : "")
    : "no image";
  const img = d.file
    ? `<img src="/${d.file}" loading="lazy" alt="${esc(d.phrase)}">`
    : `<div class="placeholder ${generating ? "working" : ""}" title="${eta ? "about " + eta + " min on this GPU" : ""}">${working}</div>`;
```

- [ ] **Step 5: Settings fields and copy**

In `static/index.html`, in the Generation panel after the model row:

```html
        <div class="row">
          <label for="poster_size_select">Poster size</label>
          <select id="poster_size_select" onchange="savePosterSize()"></select>
          <span class="hint" id="poster_size_state">50x70cm · 960x1344 is the only size measured working on this card</span>
        </div>
```

In the Connections panel after the Shop ID row:

```html
        <div class="row">
          <label>Poster blueprint ID</label>
          <input type="text" id="printify_poster_blueprint" placeholder="Printify blueprint id" style="width:140px">
          <span class="hint" id="poster_blueprint_state"></span>
        </div>
```

Change `index.html:47` from `<p class="eyebrow">T-shirt design house</p>` to `<p class="eyebrow">Print design house</p>`, and `index.html:151` from `no t-shirt template` to `no product template`.

In `static/app.js`, add the saver and load the dropdown in `loadPrompt`:

```javascript
async function savePosterSize() {
  const el = document.getElementById("poster_size_select");
  const state = document.getElementById("poster_size_state");
  try {
    await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({poster_size: el.value})});
    // the worker reads the size per image, so it lands on the next one
    state.textContent = "saved — applies to the next poster";
  } catch (e) { state.textContent = "✗ " + e.message; }
}
```

and inside `loadPrompt`:

```javascript
    const ps = document.getElementById("poster_size_select");
    ps.innerHTML = (s.poster_sizes || []).map(o =>
      `<option value="${o.value}" ${o.value === s.poster_size ? "selected" : ""}>${o.label}</option>`).join("");
    document.getElementById("poster_blueprint_state").textContent =
      s.printify_poster_blueprint_id ? "blueprint saved ✓" : "not set — posters can't publish yet";
```

Finally add `printify_poster_blueprint_id` to the body that `saveSettings()` sends, reading `document.getElementById("printify_poster_blueprint").value`.

- [ ] **Step 6: Verify by hand**

Run: `.venv\Scripts\python -m pytest -q` (nothing should have broken), then
`.venv\Scripts\uvicorn main:app --port 8000` and check in a browser:

1. Add page shows a Product dropdown with "T-shirt" and "Poster (50x70cm)".
2. Selecting Poster changes the hint to "about 20 min per image on this GPU" and swaps the Creative refinement textarea to the poster wording.
3. Settings shows a Poster size dropdown listing all seven rungs with dpi, defaulting to `960x1344 — 195 dpi`.
4. Existing cards still render square.

- [ ] **Step 7: Commit**

```bash
git add static/index.html static/app.js static/styles.css
git commit -m "feat: pick a product when queueing, and render each card as its shape"
```

---

### Task 12: Settle the composition question

**Files:**
- Modify: `probe_poster.py:30-33` (`PROMPT`) — only if the run shows duplication
- Modify: `pipeline.py` `POSTER_TEMPLATE` — only if the run shows duplication
- Test: `tests/test_pipeline.py` — only if the template changes

**Interfaces:**
- Consumes: `pipeline.POSTER_TEMPLATE` (Task 2)
- Produces: either a confirmation that no change is needed, or composition guardrails in `POSTER_TEMPLATE`

**Why this task exists:** the size probe's 960x1344 output was a 2x2 mirrored ornament — measured mirror symmetry 20.5 left-right and 38.1 top-bottom against a 55.8 random baseline. That matches the known "duplicated subject at unfamiliar aspect ratio" failure. It is **confounded**: the probe's own prompt asked for "geometric symmetrical ornamental structure", so there was no subject to duplicate. This task removes the confound. It needs the GPU and takes about 20 minutes.

- [ ] **Step 1: Generate one poster from a single-subject prompt**

```bash
.venv/Scripts/python -c "import pipeline, db; db.init(); open('probe/subject-test.png','wb').write(pipeline.generate_image_local(pipeline.build_prompt('a lighthouse on a cliff at sunset', 'art deco', 'poster'), size=(960,1344)))"
```

Expected: about 20 minutes, then `probe/subject-test.png` exists. Roughly 5 seconds of denoising and ~19 minutes of VAE decode — the long silence is normal, not a hang.

- [ ] **Step 2: Look at it and measure**

Open the file. Count the lighthouses.

```bash
.venv/Scripts/python -c "
from PIL import Image; import numpy as np
a = np.asarray(Image.open('probe/subject-test.png').convert('L'), float)
print('left-right ', abs(a - a[:, ::-1]).mean())
print('top-bottom ', abs(a - a[::-1, :]).mean())
rng = np.random.default_rng(0); b = a.copy(); rng.shuffle(b)
print('random base', abs(a - b).mean())
"
```

Expected: three numbers. A top-bottom score far below the random baseline means the halves mirror each other.

- [ ] **Step 3: Decide**

**One lighthouse, symmetry scores near the random baseline** — the aspect ratio is fine and the ornament was the old prompt doing what it was told. Record that in `probe/RESULT.txt` under the composition section, replacing the "settling it needs one run" paragraph with the result. Skip Steps 4-5.

**Two lighthouses, or a mirrored horizon** — real duplication. Continue to Step 4.

- [ ] **Step 4: Only if duplicated — add composition guardrails**

Add the test first, in `tests/test_pipeline.py`:

```python
def test_poster_template_pins_the_subject_against_duplication():
    t = pipeline.POSTER_TEMPLATE.lower()
    assert "a single" in t and "once" in t
```

Then extend `POSTER_TEMPLATE`, keeping the positive phrasing the zero-guidance
setting requires:

```python
POSTER_TEMPLATE = (
    "Fine-art poster print: {phrase}. "
    "{style}A single subject appearing exactly once, placed off-centre in the "
    "upper half, upright vertical composition, artwork running edge to edge and "
    "filling the whole canvas, clear foreground and distant background."
)
```

Re-run Step 1 to confirm the guardrails worked.

- [ ] **Step 5: Commit**

```bash
git add probe/RESULT.txt pipeline.py tests/test_pipeline.py
git commit -m "test: settle whether 5:7 duplicates the subject"
```

---

## Verification

After Task 12, from `TeeDashboard/`:

```bash
.venv/Scripts/python -m pytest -q
```

Expected: all tests pass. The suite started at 113; this plan adds roughly 45.

Then confirm the constraint that matters most — that a tee is what it always was.
Three tests assert this directly and should be named in the completion report:

- `test_build_prompt_defaults_to_the_tee_template` (Task 5) — two-argument callers
  still get the t-shirt template
- `test_step_progress_is_unchanged_for_tees` (Task 6) — the new formula is
  arithmetically identical to the old one at every step, not merely close
- `test_tee_size_is_square_and_still_obeys_speed_production` (Task 4) — the Speed
  Production toggle still reaches tee generation and only tee generation

Run them alone as a fast check:

```bash
.venv/Scripts/python -m pytest tests/ -q -k "unchanged_for_tees or defaults_to_the_tee_template or still_obeys_speed_production"
```

## Known limits at completion

State these plainly rather than letting them look finished:

- **Poster publishing is written but unproven.** The saved token 401s on `/shops.json` and on poster variant listings, so `POSTER_VARIANT_MATCH` and `price_cents: 3499` are educated guesses. Both are single-line corrections once a token with shop scope exists. The tee publish path is equally unproven, and was before this work.
- **1440x2016 is still unreachable.** 292 dpi denoises in 8 seconds but its VAE decode never finished in 23 minutes. The untested lead is raising `tile_latent_min_size`, which could cut decode roughly 3x. Not in this plan.
- **195 dpi is below a typical 300 dpi print spec**, above the 150 dpi large-format floor.
