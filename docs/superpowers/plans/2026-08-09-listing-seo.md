# Etsy Listing Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every published design carries a keyword-dense Etsy title, 13 search tags, and a description whose opening paragraph is unique to that artwork.

**Architecture:** A new `listing.py` mirrors the existing `refine.py`: one Gemma call behind a settings-editable prompt, forgiving prefix-label parsing, limits enforced in Python rather than trusted to the model. It is called fire-and-forget on approve, exactly as `upscale.upscale` already is. Results land in three new `designs` columns, are editable in the review card, and are read by `printify.publish` with today's behaviour as the per-field fallback.

**Tech Stack:** Python 3.12, FastAPI, SQLite (stdlib `sqlite3`), `google-genai` (Gemma via the Gemini API), pytest, vanilla JS front end.

## Global Constraints

- Etsy caps, single constants in `listing.py`: title **140** characters, **13** tags, **20** characters per tag. Taken from Etsy documentation, not verified against this account — see "Open question" in the spec.
- A copywriting failure must NEVER block a publish. Every field falls back to today's behaviour independently.
- `designs.tags` is the operator's own filing label (`app.js:17-19`, Top Styles chart, Library filter). It is never published. Etsy tags live in `listing_tags`. **Do not merge them.**
- `listing.generate` returns a dict where a failed field is **absent**, not empty. Callers use `.get()`, never indexing.
- Factual claims (paper, size, framing, shipping, returns) come from the operator's `listing_boilerplate` setting only. The model is instructed never to write them.
- Follow existing house style: no new dependencies, `%`-formatting for SQL/messages as the repo does, tests use `monkeypatch` + `tmp_path` with no network.
- **`clamp_title` and `clean_tags` are the single enforcement point for Etsy's limits.** They are public (no leading underscore) because `main.py` and `printify.py` both call them. Never re-implement splitting, lower-casing, de-duplication or the `[:13]` cut anywhere else, and never write the literal `13`, `20` or `140` outside `listing.py`.
- **This worktree has no `.venv`.** Every `.venv/bin/pytest ...` command in this plan must be run as `/Users/dwightbritton/Desktop/Tshirt-dashboard/.venv/bin/pytest ...` from the worktree root. The first invocation is slow (several minutes, iCloud rehydration); later ones are fast. This is expected — do not treat it as a hang.
- `save_settings` (`main.py:344-346`) skips empty values, so a text setting can be overwritten but not cleared to empty. This is pre-existing behaviour; do not change it in this plan.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `listing.py` | Prompt, Gemma call, parsing, limit enforcement, threaded write | **Create** |
| `tests/test_listing.py` | Parsing and limit rules, generate fallbacks, recent_tags | **Create** |
| `db.py` | Three new columns via `MIGRATIONS` | Modify |
| `printify.py` | Consume stored copy, send `tags`, assemble description | Modify |
| `main.py` | Approve hook, PATCH fields, settings body + GET | Modify |
| `static/app.js` | Focus guard, card listing section, settings wiring | Modify |
| `static/index.html` | Settings panel for the three new settings | Modify |
| `tests/test_db.py`, `tests/test_printify.py`, `tests/test_api.py` | Coverage for the above | Modify |

---

### Task 1: `listing.py` parsing and limit rules

Pure functions only — no network, no database. This is the task that makes the model's sloppy output safe.

**Files:**
- Create: `listing.py`
- Create: `tests/test_listing.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TITLE_MAX = 140`, `TAG_MAX = 20`, `TAG_COUNT = 13`; `_parse(text: str) -> dict[str, str]`; `clamp_title(s: str) -> str`; `clean_tags(raw: str) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_listing.py`:

```python
import listing


def test_parse_reads_the_three_labels():
    text = "TITLE: Vintage Fishing Print, Angler Gift\nTAGS: fishing print, angler gift\nHOOK: A weathered angler."
    assert listing._parse(text) == {
        "title": "Vintage Fishing Print, Angler Gift",
        "tags": "fishing print, angler gift",
        "hook": "A weathered angler.",
    }


def test_parse_ignores_unlabelled_chatter_and_is_case_insensitive():
    text = "Sure, here you go!\ntitle: A Print\nnonsense line\nHOOK: Some art."
    assert listing._parse(text) == {"title": "A Print", "hook": "Some art."}


def test_parse_keeps_the_first_of_a_repeated_label():
    assert listing._parse("TITLE: first\nTITLE: second")["title"] == "first"


def test_parse_skips_a_label_with_no_value():
    assert listing._parse("TITLE:   \nHOOK: real") == {"hook": "real"}


def test_clamp_title_leaves_a_short_title_alone():
    assert listing.clamp_title("  A Short Title  ") == "A Short Title"


def test_clamp_title_truncates_on_a_comma_boundary():
    long = ", ".join(["keyword phrase here"] * 12)   # well over 140
    out = listing.clamp_title(long)
    assert len(out) <= listing.TITLE_MAX
    assert not out.endswith(",")
    assert out in long          # never invents characters
    assert out.endswith("here") # never ends mid-word


def test_clamp_title_hard_cuts_when_there_is_no_comma_in_range():
    out = listing.clamp_title("x" * 200)
    assert out == "x" * listing.TITLE_MAX


def test_clean_tags_lowercases_trims_and_dedupes():
    assert listing.clean_tags("Fishing Print, fishing print ,  Angler Gift ") == [
        "fishing print", "angler gift"]


def test_clean_tags_drops_tags_over_the_character_cap():
    assert listing.clean_tags("ok tag, this tag is far too long to be allowed") == ["ok tag"]


def test_clean_tags_caps_the_count_at_thirteen():
    raw = ", ".join("tag%d" % i for i in range(20))
    assert len(listing.clean_tags(raw)) == listing.TAG_COUNT


def test_clean_tags_of_nothing_is_empty():
    assert listing.clean_tags("") == []
    assert listing.clean_tags("  ,  , ") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'listing'`

- [ ] **Step 3: Write the minimal implementation**

Create `listing.py`:

```python
"""Etsy listing copy - title, search tags, and description hook - from Gemma.

Same shape as refine.py: one call behind a settings-editable prompt, loose
parsing, and a caller that decides what a failure means. The limits below are
enforced here rather than trusted to the model: asked for "13 tags under 20
characters" it returns 15 tags and a 24-character one.
"""

# Etsy's caps. Documented values, not verified against this account.
TITLE_MAX = 140
TAG_MAX = 20
TAG_COUNT = 13

_LABELS = ("TITLE", "TAGS", "HOOK")


def _parse(text: str) -> dict[str, str]:
    """Pull the labelled lines out of a Gemma response.

    Prefix labels rather than JSON: a small model that drops a brace loses the
    whole parse, where a dropped label loses one field. First occurrence wins -
    a model that restates itself shouldn't overwrite its own better answer.
    """
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        for label in _LABELS:
            key = label.lower()
            if key in out or not line.upper().startswith(label + ":"):
                continue
            value = line[len(label) + 1:].strip()
            if value:
                out[key] = value
    return out


def clamp_title(s: str) -> str:
    """At most TITLE_MAX characters, cut on a comma so it never ends mid-word."""
    s = s.strip()
    if len(s) <= TITLE_MAX:
        return s
    cut = s[:TITLE_MAX]
    comma = cut.rfind(",")
    return (cut[:comma] if comma > 0 else cut).strip()


def clean_tags(raw: str) -> list[str]:
    """Lower-cased, de-duplicated, over-long ones dropped, capped at TAG_COUNT.

    Dropping rather than truncating an over-long tag is deliberate: half a
    keyword is not a keyword, and Etsy would reject the listing anyway.
    """
    out = []
    for tag in raw.split(","):
        tag = tag.strip().strip('"').lower()
        if tag and len(tag) <= TAG_MAX and tag not in out:
            out.append(tag)
    return out[:TAG_COUNT]


if __name__ == "__main__":
    assert _parse("TITLE: a\nTAGS: b\nHOOK: c") == {"title": "a", "tags": "b", "hook": "c"}
    assert _parse("TITLE: first\nTITLE: second")["title"] == "first"
    assert _parse("TITLE:  \nHOOK: real") == {"hook": "real"}
    assert clamp_title("x" * 200) == "x" * TITLE_MAX
    assert clamp_title("aaa, bbb" + ", ccc" * 60).endswith("ccc")
    assert len(clamp_title("aaa, bbb" + ", ccc" * 60)) <= TITLE_MAX
    assert clean_tags("A, a, B") == ["a", "b"]
    assert clean_tags("x" * 21) == []
    assert len(clean_tags(", ".join("t%d" % i for i in range(30)))) == TAG_COUNT
    print("listing self-check ok")
```

- [ ] **Step 4: Run the tests and the self-check**

Run: `.venv/bin/pytest tests/test_listing.py -v`
Expected: PASS, 11 tests

Run: `.venv/bin/python listing.py`
Expected: `listing self-check ok`

- [ ] **Step 5: Commit**

```bash
git add listing.py tests/test_listing.py
git commit -m "feat: parse and clamp Etsy listing copy from Gemma output"
```

---

### Task 2: `listing.generate` — the Gemma call

**Files:**
- Modify: `listing.py`
- Modify: `tests/test_listing.py`

**Interfaces:**
- Consumes: `_parse`, `clamp_title`, `clean_tags` from Task 1.
- Produces: `DEFAULT_LISTING_PROMPT: str`; `DEFAULTS: dict[str, str]`; `generate(phrase: str, filters: str, product: str | None, context: str, recent_tags: list[str], system_prompt: str) -> dict`. Returned dict has keys `title` (str), `tags` (list[str]), `hook` (str) — **each present only if it survived parsing**.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_listing.py`:

```python
import db
import pipeline
import pytest


class _FakeModels:
    def __init__(self, text): self.text = text; self.seen = None
    def generate_content(self, model, contents):
        self.seen = contents
        return type("R", (), {"text": self.text})()


def _fake_genai(monkeypatch, text):
    """Stand in for google.genai so no network or key is needed."""
    models = _FakeModels(text)
    client = type("C", (), {"models": models})()
    monkeypatch.setattr(listing, "_client", lambda key: client)
    return models


def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    db.set_setting("gemini_api_key", "k")


def test_generate_returns_all_three_fields(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    _fake_genai(monkeypatch, "TITLE: A Print, Wall Art\nTAGS: a tag, b tag\nHOOK: Nice art.")
    out = listing.generate("lighthouse", "vintage", "poster", "", [],
                           listing.DEFAULT_LISTING_PROMPT)
    assert out == {"title": "A Print, Wall Art", "tags": ["a tag", "b tag"], "hook": "Nice art."}


def test_generate_omits_a_field_it_could_not_parse(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    _fake_genai(monkeypatch, "TITLE: Only A Title")
    out = listing.generate("lighthouse", "", "poster", "", [],
                           listing.DEFAULT_LISTING_PROMPT)
    assert out == {"title": "Only A Title"}
    assert "hook" not in out and "tags" not in out


def test_generate_raises_when_nothing_parses(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    _fake_genai(monkeypatch, "Sorry, I cannot help with that.")
    with pytest.raises(RuntimeError, match="no usable"):
        listing.generate("x", "", "tee", "", [], listing.DEFAULT_LISTING_PROMPT)


def test_generate_raises_without_a_key(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db.init()
    with pytest.raises(RuntimeError, match="key"):
        listing.generate("x", "", "tee", "", [], listing.DEFAULT_LISTING_PROMPT)


def test_generate_names_the_product_in_the_prompt(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    models = _fake_genai(monkeypatch, "TITLE: t")
    listing.generate("lighthouse", "", "poster", "", [], listing.DEFAULT_LISTING_PROMPT)
    assert pipeline.PRODUCTS["poster"]["label"] in models.seen


def test_generate_includes_context_and_avoided_tags_when_given(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    models = _fake_genai(monkeypatch, "TITLE: t")
    listing.generate("lighthouse", "bold", "tee", "gifts for new homes", ["old tag"],
                     listing.DEFAULT_LISTING_PROMPT)
    assert "gifts for new homes" in models.seen
    assert "old tag" in models.seen
    assert "bold" in models.seen


def test_generate_omits_those_lines_when_empty(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    models = _fake_genai(monkeypatch, "TITLE: t")
    listing.generate("lighthouse", "", "tee", "", [], listing.DEFAULT_LISTING_PROMPT)
    assert "Shop context" not in models.seen
    assert "Avoid reusing" not in models.seen


def test_default_prompt_forbids_invented_facts():
    p = listing.DEFAULT_LISTING_PROMPT.lower()
    for banned in ("paper", "shipping", "framing", "returns"):
        assert banned in p, "the prompt must name %s as something never to write" % banned


def test_defaults_cover_the_settings_key():
    assert listing.DEFAULTS["listing_prompt"] is listing.DEFAULT_LISTING_PROMPT
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listing.py -v`
Expected: FAIL — `AttributeError: module 'listing' has no attribute 'DEFAULT_LISTING_PROMPT'`

- [ ] **Step 3: Write the minimal implementation**

Add to `listing.py`, after `clean_tags` and before `__main__`:

```python
DEFAULT_LISTING_PROMPT = (
    "You write Etsy listings for a print-on-demand shop. Given a design concept "
    "for a {product}, reply with exactly three lines and nothing else:\n"
    "TITLE: up to 140 characters of comma-separated keyword phrases a shopper "
    "would actually type, most valuable phrase first.\n"
    "TAGS: exactly 13 comma-separated search keywords, each under 20 characters, "
    "lower case, no repetition of the same word.\n"
    "HOOK: two or three sentences describing this specific artwork to a shopper. "
    "Describe only what is in the image. Never mention paper, size, framing, "
    "shipping or returns - those are added separately and anything you invent "
    "about them would be a false promise to a buyer."
)

# Keyed by settings key, same habit as refine.DEFAULTS: main can resolve
# "saved value or default" with one lookup.
DEFAULTS = {"listing_prompt": DEFAULT_LISTING_PROMPT}


def _client(key: str):
    """Split out so tests can stand in for the SDK without touching the network."""
    from google import genai

    return genai.Client(api_key=key)


def generate(phrase: str, filters: str, product: str | None, context: str,
             recent_tags: list[str], system_prompt: str) -> dict:
    """One Gemma call -> {title, tags, hook}, each key present only if usable.

    Raises RuntimeError when nothing at all parsed, so the caller can fall back
    wholesale - the same contract as refine.refine.
    """
    key = db.get_setting("gemini_api_key")
    if not key:
        raise RuntimeError("No Gemini API key configured")

    label = pipeline.product_data(product)["label"]
    # Gemma on the Gemini API has no system role, so fold it into the content.
    system = system_prompt.replace("{product}", label)
    brief = phrase if not filters else "%s\nStyle keywords: %s" % (phrase, filters)
    if context.strip():
        brief += "\nShop context: %s" % context.strip()
    if recent_tags:
        brief += "\nAvoid reusing these tags: %s" % ", ".join(recent_tags)

    resp = _client(key).models.generate_content(
        model=refine.GEMMA_MODEL,
        contents="%s\n\nWrite the listing for this design:\n%s" % (system, brief),
    )

    fields = _parse(resp.text or "")
    out = {}
    if title := clamp_title(fields.get("title", "")):
        out["title"] = title
    if tags := clean_tags(fields.get("tags", "")):
        out["tags"] = tags
    if hook := fields.get("hook", "").strip():
        out["hook"] = hook
    if not out:
        raise RuntimeError("Gemma returned no usable listing copy")
    return out
```

Add the imports this task needs at the top of `listing.py`, below the docstring:

```python
import db
import pipeline
import refine
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_listing.py -v`
Expected: PASS, 20 tests

- [ ] **Step 5: Commit**

```bash
git add listing.py tests/test_listing.py
git commit -m "feat: generate Etsy listing copy with one Gemma call"
```

---

### Task 3: schema columns and settings plumbing

**Files:**
- Modify: `db.py` (the `MIGRATIONS` tuple)
- Modify: `main.py:81-94` (`SettingsBody`), `main.py:319-339` (`get_settings`)
- Modify: `tests/test_db.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `listing.DEFAULT_LISTING_PROMPT` from Task 2.
- Produces: `designs.listing_title`, `designs.listing_tags`, `designs.listing_hook`; settings keys `listing_prompt`, `listing_boilerplate`, `shop_context`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
def test_listing_columns_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
    assert {"listing_title", "listing_tags", "listing_hook"} <= cols


def test_listing_tags_defaults_to_empty_string_not_null(tmp_path, monkeypatch):
    # printify splits this on "," - a NULL would be a TypeError at publish time
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase) VALUES ('x')")
        row = con.execute("SELECT listing_tags FROM designs").fetchone()
    assert row["listing_tags"] == ""


def test_listing_columns_are_added_to_a_pre_existing_table(tmp_path, monkeypatch):
    # the migration path real databases take, not the fresh-schema one
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    with db.connect() as con:
        con.execute("CREATE TABLE designs (id INTEGER PRIMARY KEY, phrase TEXT NOT NULL)")
    db.init()
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
    assert {"listing_title", "listing_tags", "listing_hook"} <= cols
```

Append to `tests/test_api.py`. This file calls endpoint functions directly via
the `load_main` / `insert` helpers at its top — there is no HTTP client fixture,
so follow that convention exactly:

```python
def test_settings_roundtrips_the_listing_keys(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    main.save_settings(main.SettingsBody(
        listing_boilerplate="Printed on 200gsm matte.",
        shop_context="wall art for first-home buyers",
        listing_prompt="custom prompt",
    ))
    out = main.get_settings()
    assert out["listing_boilerplate"] == "Printed on 200gsm matte."
    assert out["shop_context"] == "wall art for first-home buyers"
    assert out["listing_prompt"] == "custom prompt"


def test_listing_prompt_falls_back_to_the_default(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["listing_prompt"] == listing.DEFAULT_LISTING_PROMPT


def test_boilerplate_and_context_default_to_empty(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    out = main.get_settings()
    assert out["listing_boilerplate"] == ""
    assert out["shop_context"] == ""
```

Add `import listing` to the imports of `tests/test_api.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_db.py tests/test_api.py -v -k "listing or boilerplate or context"`
Expected: FAIL — the columns are missing and `get_settings` has no such keys

- [ ] **Step 3: Write the minimal implementation**

In `db.py`, append three entries to the end of the `MIGRATIONS` tuple:

```python
    ("listing_title", "ALTER TABLE designs ADD COLUMN listing_title TEXT"),
    ("listing_tags", "ALTER TABLE designs ADD COLUMN listing_tags TEXT NOT NULL DEFAULT ''"),
    ("listing_hook", "ALTER TABLE designs ADD COLUMN listing_hook TEXT"),
```

In `main.py`, add `import listing` alongside the other local imports, then add three fields to the end of `SettingsBody`:

```python
    listing_prompt: str = ""
    listing_boilerplate: str = ""
    shop_context: str = ""
```

In `get_settings`, before `return out`:

```python
    out["listing_prompt"] = db.get_setting("listing_prompt") or listing.DEFAULT_LISTING_PROMPT
    out["listing_boilerplate"] = db.get_setting("listing_boilerplate") or ""
    out["shop_context"] = db.get_setting("shop_context") or ""
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_db.py tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db.py main.py tests/test_db.py tests/test_api.py
git commit -m "feat: store listing copy on designs; add listing settings keys"
```

---

### Task 4: publish the stored copy and the tags

This task fixes the live bug: `printify.py:110` sets `"tags": True` but the product payload never includes a `tags` key, so every listing publishes with zero tags.

**Files:**
- Modify: `printify.py:81-107` (the product payload), add `_description`
- Modify: `tests/test_printify.py`

**Interfaces:**
- Consumes: `designs.listing_title`, `designs.listing_tags`, `designs.listing_hook` and the `listing_boilerplate` setting from Task 3.
- Produces: `printify._description(design: dict) -> str`.

- [ ] **Step 1: Write the failing tests**

Add `import listing` to `tests/test_printify.py`, then append to it (reusing
`setup_tmp`, `_fake_api`, `TEE_VARIANTS` already in that file):

```python
def _publish(tmp_path, monkeypatch, design):
    src = tmp_path / "art.png"
    src.write_bytes(b"PNG")
    calls = []
    _fake_api(monkeypatch, calls, TEE_VARIANTS)
    base = {"id": 1, "phrase": "dog dad", "product": "tee",
            "file": str(src), "print_file": None}
    printify.publish({**base, **design})
    return [p for path, p in calls if path.endswith("/products.json")][0]


def test_publish_sends_the_stored_title_and_tags(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    call = _publish(tmp_path, monkeypatch, {
        "listing_title": "Dog Dad Print, Funny Gift",
        "listing_tags": "dog dad,funny gift,pet lover",
    })
    assert call["title"] == "Dog Dad Print, Funny Gift"
    assert call["tags"] == ["dog dad", "funny gift", "pet lover"]


def test_publish_caps_the_tags_at_thirteen(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    call = _publish(tmp_path, monkeypatch, {
        "listing_tags": ",".join("tag%d" % i for i in range(20))})
    assert len(call["tags"]) == listing.TAG_COUNT


def test_publish_reapplies_the_limits_to_whatever_was_stored(tmp_path, monkeypatch):
    # publish is the last gate: a row edited outside the app still can't ship
    # an over-long or duplicated tag
    setup_tmp(tmp_path, monkeypatch)
    call = _publish(tmp_path, monkeypatch, {
        "listing_tags": "Dog Dad, dog dad, %s" % ("x" * 25)})
    assert call["tags"] == ["dog dad"]


def test_publish_sends_an_empty_tag_list_when_there_is_no_copy(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    call = _publish(tmp_path, monkeypatch, {})
    assert call["tags"] == []


def test_publish_falls_back_to_the_old_title_without_stored_copy(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    call = _publish(tmp_path, monkeypatch, {})
    assert call["title"] == "Dog Dad T-Shirt"
    assert call["description"] == "dog dad"


def test_description_joins_the_hook_and_the_boilerplate(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("listing_boilerplate", "Printed on 200gsm matte.")
    assert printify._description({"phrase": "dog dad", "listing_hook": "A good dog."}) == \
        "A good dog.\n\nPrinted on 200gsm matte."


def test_description_uses_whichever_half_exists(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert printify._description({"phrase": "dog dad", "listing_hook": "A good dog."}) == \
        "A good dog."
    db.set_setting("listing_boilerplate", "200gsm matte.")
    assert printify._description({"phrase": "dog dad", "listing_hook": None}) == "200gsm matte."


def test_description_falls_back_to_the_phrase_when_both_are_empty(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert printify._description({"phrase": "dog dad", "listing_hook": None}) == "dog dad"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_printify.py -v -k "tags or description or stored"`
Expected: FAIL — `AttributeError: module 'printify' has no attribute '_description'`, and `KeyError: 'tags'` on the payload

- [ ] **Step 3: Write the minimal implementation**

Add to `printify.py`, above `publish`:

```python
def _description(design: dict) -> str:
    """Generated hook plus the operator's fixed block.

    The boilerplate is a setting rather than model output on purpose: paper
    weight, sizes and delivery are promises to a buyer, and a model asked to
    write them invents them.
    """
    parts = [p.strip() for p in (design.get("listing_hook"),
                                 db.get_setting("listing_boilerplate")) if p and p.strip()]
    return "\n\n".join(parts) if parts else design["phrase"]
```

In `publish`, replace the `title` and `description` entries of the product payload and add `tags`:

```python
            "title": design.get("listing_title")
                     or design["phrase"].title() + " " + data["title_suffix"],
            "description": _description(design),
            "tags": listing.clean_tags(design.get("listing_tags") or ""),
```

Add `import listing` to `printify.py`'s imports. Reusing `clean_tags` rather
than splitting inline is deliberate: it is the single place that knows Etsy's
limits, so publish cannot drift from what generation and editing enforce, and
the literal `13` never appears here.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_printify.py -v`
Expected: PASS — including the pre-existing publish tests, which must be unchanged

- [ ] **Step 5: Commit**

```bash
git add printify.py tests/test_printify.py
git commit -m "fix: publish Etsy tags and the assembled description"
```

---

### Task 5: generate the copy on approve

**Files:**
- Modify: `listing.py` (add `recent_tags` and `write`)
- Modify: `main.py:231-239` (the approve endpoint)
- Modify: `tests/test_listing.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `listing.generate` from Task 2; the columns from Task 3.
- Produces: `listing.recent_tags(limit: int = 20) -> list[str]`; `listing.write(design_id: int) -> None` (fire-and-forget, returns immediately).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_listing.py`:

```python
def _row(design_id=1):
    with db.connect() as con:
        return dict(con.execute("SELECT * FROM designs WHERE id = ?", (design_id,)).fetchone())


def _seed(phrase="dog dad", status="approved"):
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, product, status) VALUES (?,?,?,?)",
                    (phrase, "", "tee", status))


def test_write_stores_all_three_fields(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    _seed()
    monkeypatch.setattr(listing, "generate",
                        lambda *a, **k: {"title": "T", "tags": ["a", "b"], "hook": "H"})
    listing.write(1)
    row = _row()
    assert row["listing_title"] == "T"
    assert row["listing_tags"] == "a,b"
    assert row["listing_hook"] == "H"


def test_write_records_the_error_and_leaves_the_design_alone_on_failure(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    _seed()
    def boom(*a, **k): raise RuntimeError("quota exhausted")
    monkeypatch.setattr(listing, "generate", boom)
    listing.write(1)
    row = _row()
    assert "quota exhausted" in row["error"]
    assert row["status"] == "approved"      # never un-approves
    assert row["listing_title"] is None


def test_write_is_a_no_op_for_a_missing_design(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    listing.write(999)      # must not raise


def test_recent_tags_reads_published_designs_newest_first(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, status, listing_tags) VALUES ('a','published','one,two')")
        con.execute("INSERT INTO designs (phrase, status, listing_tags) VALUES ('b','published','two,three')")
        con.execute("INSERT INTO designs (phrase, status, listing_tags) VALUES ('c','approved','ignored')")
    assert listing.recent_tags() == ["two", "three", "one"]


def test_recent_tags_is_empty_before_anything_is_published(tmp_path, monkeypatch):
    _db(tmp_path, monkeypatch)
    _seed()
    assert listing.recent_tags() == []
```

Note: `listing.write` spawns a daemon thread, so a bare call would race the
assertions. Add this helper to `tests/test_listing.py` and use it in place of
every `listing.write(N)` call in the four tests above — replace `listing.write(1)`
with `_write_sync(monkeypatch, 1)` and `listing.write(999)` with
`_write_sync(monkeypatch, 999)`:

```python
class _NowThread:
    """A Thread stand-in that runs the job inline when .start() is called."""
    def __init__(self, target, daemon=None): self.target = target
    def start(self): self.target()


def _write_sync(monkeypatch, design_id):
    """Run listing.write's job on this thread so assertions see the result."""
    monkeypatch.setattr(listing.threading, "Thread", _NowThread)
    listing.write(design_id)
```

This works because `listing.write` calls `threading.Thread(target=job,
daemon=True).start()` — patching the class means `.start()` runs `job` inline.

Append to `tests/test_api.py`, in the same direct-call style as
`test_approve_sets_reviewed_at` already in that file:

```python
def test_approve_kicks_off_listing_copy(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    called = []
    monkeypatch.setattr(main.listing, "write", lambda design_id: called.append(design_id))
    did = insert("pending")
    main.approve(did)
    assert called == [did]


def test_a_listing_failure_does_not_break_approve(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    def boom(design_id): raise RuntimeError("gemma down")
    monkeypatch.setattr(main.listing, "write", boom)
    did = insert("pending")
    with pytest.raises(RuntimeError):
        main.approve(did)
    # the design is still approved - the status write happens before the call
    with db.connect() as con:
        row = con.execute("SELECT status FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "approved"
```

Note: `listing.write` catches everything inside its own thread, so in practice
it cannot raise into `approve`. The second test pins that the ordering in
`approve` would survive it anyway.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_listing.py tests/test_api.py -v -k "write or recent_tags or approve"`
Expected: FAIL — `AttributeError: module 'listing' has no attribute 'write'`

- [ ] **Step 3: Write the minimal implementation**

Add `import threading` as the first import in `listing.py`, above the local
imports, so the block reads:

```python
import threading

import db
import pipeline
import refine
```

Then add to the end of `listing.py`, before `__main__`:

```python
def recent_tags(limit: int = 20) -> list[str]:
    """Tags already used on recently published designs, newest first.

    Etsy ranks down shops that repeat the same tags across listings, so these
    are handed to the model as terms to vary from. Returns [] until something
    has actually been published, which is why no caller needs a branch.
    """
    with db.connect() as con:
        rows = con.execute(
            "SELECT listing_tags FROM designs WHERE status = 'published' "
            "AND listing_tags != '' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for row in rows:
        for tag in row["listing_tags"].split(","):
            tag = tag.strip()
            if tag and tag not in out:
                out.append(tag)
    return out


def write(design_id: int) -> None:
    """Fire-and-forget: generate this design's listing copy and store it.

    Same shape and same rule as upscale.upscale - the design stays approved and
    publish falls back to the old title, because a copywriting failure must
    never block a publish.
    """

    def job():
        try:
            with db.connect() as con:
                row = con.execute(
                    "SELECT phrase, filters, product FROM designs WHERE id = ?", (design_id,)
                ).fetchone()
            if not row:
                return
            out = generate(
                row["phrase"], row["filters"], row["product"],
                db.get_setting("shop_context") or "",
                recent_tags(),
                db.get_setting("listing_prompt") or DEFAULT_LISTING_PROMPT,
            )
            with db.connect() as con:
                con.execute(
                    "UPDATE designs SET listing_title = ?, listing_tags = ?, "
                    "listing_hook = ?, error = NULL WHERE id = ?",
                    (out.get("title", ""), ",".join(out.get("tags", [])),
                     out.get("hook", ""), design_id),
                )
        except Exception as e:
            with db.connect() as con:
                con.execute(
                    "UPDATE designs SET error = ? WHERE id = ?",
                    (("listing copy failed: %s" % e)[:500], design_id),
                )

    threading.Thread(target=job, daemon=True).start()
```

In `main.py`, extend the approve endpoint (`main.py:231-239`) so it reads:

```python
@app.post("/api/designs/{design_id}/approve")
def approve(design_id: int, _gate: None = Depends(require_access_code)):
    _set_status(design_id, "approved", ("pending",))
    with db.connect() as con:
        con.execute("UPDATE designs SET reviewed_at = datetime('now') WHERE id = ?", (design_id,))
        row = con.execute("SELECT file FROM designs WHERE id = ?", (design_id,)).fetchone()
    if row and row["file"]:
        upscale.upscale(design_id, os.path.join(BASE, row["file"]))
    listing.write(design_id)
    return {"ok": True}
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest -q`
Expected: PASS, whole suite

- [ ] **Step 5: Commit**

```bash
git add listing.py main.py tests/test_listing.py tests/test_api.py
git commit -m "feat: write listing copy in the background when a design is approved"
```

---

### Task 6: edit the copy through the existing PATCH route

**Files:**
- Modify: `main.py:71-73` (`PatchBody`), `main.py:180-197` (`patch_design`)
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `listing.clean_tags` from Task 1; the columns from Task 3.
- Produces: `PATCH /api/designs/{id}` accepting `listing_title`, `listing_tags`, `listing_hook`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`, matching `test_patch_tags_and_rating`:

```python
def _read(did):
    with db.connect() as con:
        return con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()


def test_patch_saves_the_listing_fields(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("approved")
    main.patch_design(did, main.PatchBody(
        listing_title="  A Better Title  ", listing_hook="Hand-drawn."))
    row = _read(did)
    assert row["listing_title"] == "A Better Title"
    assert row["listing_hook"] == "Hand-drawn."


def test_patch_applies_the_tag_limits_to_hand_typed_tags(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("approved")
    main.patch_design(did, main.PatchBody(
        listing_tags="Dog Dad, dog dad, %s, ok" % ("x" * 25)))
    assert _read(did)["listing_tags"] == "dog dad,ok"


def test_patch_clamps_a_hand_typed_title(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("approved")
    main.patch_design(did, main.PatchBody(listing_title="y" * 200))
    assert len(_read(did)["listing_title"]) == listing.TITLE_MAX


def test_patch_can_clear_a_listing_field(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("approved")
    main.patch_design(did, main.PatchBody(listing_hook="something"))
    main.patch_design(did, main.PatchBody(listing_hook=""))
    assert _read(did)["listing_hook"] == ""
```

The pre-existing `test_patch_empty_body_400` already covers the empty-body case
and must keep passing — `PatchBody()` with all five fields defaulting to `None`
still produces no `sets`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -v -k patch`
Expected: FAIL — the fields are ignored, so `listing_title` stays `None` and the 400 test passes vacuously

- [ ] **Step 3: Write the minimal implementation**

Extend `PatchBody`:

```python
class PatchBody(BaseModel):
    tags: str | None = None
    rating: int | None = None
    listing_title: str | None = None
    listing_tags: str | None = None
    listing_hook: str | None = None
```

In `patch_design`, after the existing `rating` block and before `if not sets:`:

```python
    if body.listing_title is not None:
        sets.append("listing_title = ?")
        vals.append(listing.clamp_title(body.listing_title))
    if body.listing_tags is not None:
        # hand-typed tags go through the same limits as generated ones
        sets.append("listing_tags = ?")
        vals.append(",".join(listing.clean_tags(body.listing_tags)))
    if body.listing_hook is not None:
        sets.append("listing_hook = ?")
        vals.append(body.listing_hook.strip())
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_api.py
git commit -m "feat: edit listing copy through the design PATCH route"
```

---

### Task 7: front end — focus guard, card section, settings panel

**Files:**
- Modify: `static/app.js:383-405` (`syncChildren`), `:453-481` (`card`), `:270` (`saveSettings`) and its settings loader
- Modify: `static/index.html` (a new Settings panel)

**Interfaces:**
- Consumes: the PATCH fields from Task 6 and the settings keys from Task 3.
- Produces: no JS exports; `saveListing(id, btn)` is called from inline `onclick` in the card markup.

- [ ] **Step 1: Add the focus guard to `syncChildren`**

`syncChildren` replaces a card whenever its HTML signature changes (`app.js:392`). The grid re-polls every 3 seconds, so without this guard a background copy landing — or any sibling card changing — destroys half-typed text in a card's textarea.

In `static/app.js`, inside `syncChildren`, change the replace branch:

```javascript
    if (el) {
      old.delete(it.key);
      // Never rebuild a card the user is typing in: replaceWith would drop the
      // element under the cursor and lose the edit. It re-syncs on the next
      // poll after focus moves away.
      const busy = el.contains(document.activeElement);
      if (el.__sig !== it.html && !busy) {
        const fresh = buildEl(it);
        adoptImage(el, fresh);
        el.replaceWith(fresh);
        el = fresh;
      }
    } else {
```

- [ ] **Step 2: Check the guard reads correctly**

Do **not** start a server. Browser verification is the controller's job after
all seven tasks land; starting uvicorn here costs several minutes and the
database has no designs to render.

Instead confirm by reading: `busy` is computed from the element being replaced
(`el`), not the freshly built one; the guard short-circuits only the
`replaceWith` branch; and the `insertBefore` reordering below it still runs so
a focused card can still move position.

- [ ] **Step 3: Commit the guard on its own**

```bash
git add static/app.js
git commit -m "fix: don't rebuild a card the user is typing in"
```

- [ ] **Step 4: Add the listing section to the card**

In `static/app.js`, add above `function card(d)`:

```javascript
// Approved designs carry their Etsy copy here. Collapsed by default so the
// review grid stays a grid; the fields save on blur through the same PATCH
// route that already handles tags and rating.
function listingBox(d) {
  if (d.status !== "approved" && d.status !== "published") return "";
  const waiting = !d.listing_title && !d.listing_hook && !d.listing_tags;
  if (waiting && !d.error) return `<div class="prompt none">writing listing copy…</div>`;
  return `<details class="prompt listing"><summary>listing copy</summary>` +
    `<label>Title</label><textarea data-f="listing_title" data-id="${d.id}" rows="2">${esc(d.listing_title || "")}</textarea>` +
    `<label>Tags (13 max)</label><textarea data-f="listing_tags" data-id="${d.id}" rows="2">${esc(d.listing_tags || "")}</textarea>` +
    `<label>Hook</label><textarea data-f="listing_hook" data-id="${d.id}" rows="4">${esc(d.listing_hook || "")}</textarea>` +
    `</details>`;
}
```

In `card(d)`, insert it into the returned markup after `promptLine(d)`:

```javascript
    promptLine(d) +
    listingBox(d) +
    (d.error ? `<div class="error">${esc(d.error)}</div>` : "") +
```

Add one delegated listener near the other top-level listeners in `app.js` (a single listener rather than an inline handler per field, so it survives every card rebuild):

```javascript
// Save a listing field when the user leaves it. Delegated from document so it
// keeps working across card rebuilds. blur does not bubble, hence capture.
document.addEventListener("blur", async e => {
  const t = e.target;
  if (!t.dataset || !t.dataset.f || !t.dataset.id) return;
  try {
    await api(`/api/designs/${t.dataset.id}`, {
      method: "PATCH",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({[t.dataset.f]: t.value}),
    });
    refresh();
  } catch (err) { flash("Couldn't save the listing copy — " + err.message); }
}, true);
```

This uses the file's existing `api()` fetch wrapper, `refresh()` (the 3-second
poll, wired at `app.js:937`) and `flash()` for the error toast — not bare
`fetch`, so the access-code header handling in `api()` still applies.

- [ ] **Step 5: Add the Settings panel**

In `static/index.html`, add a new panel inside the settings view, after the existing "Generation" panel:

```html
      <section class="panel">
        <div class="panel-label">Listing copy</div>
        <label>Shop context — your niche and who buys, used to write better tags. Never published.</label>
        <textarea id="shop_context" spellcheck="false" placeholder="wall art for UK first-home buyers, housewarming and new-baby gifts"></textarea>
        <label>Description boilerplate — added under every listing. Paper, sizes, framing, shipping, returns.</label>
        <textarea id="listing_boilerplate" spellcheck="false" placeholder="Printed on 200gsm matte art paper…"></textarea>
        <label>System prompt — how Gemma writes your titles, tags and hooks</label>
        <textarea id="listing_prompt" spellcheck="false"></textarea>
        <div class="row"><button onclick="saveSettings()">Save</button></div>
      </section>
```

In `static/app.js`, add the three fields to the `body` object inside
`saveSettings` (`app.js:272-278`), after `access_code`:

```javascript
    shop_context: document.getElementById("shop_context").value,
    listing_boilerplate: document.getElementById("listing_boilerplate").value,
    listing_prompt: document.getElementById("listing_prompt").value,
```

And populate them in `loadPrompt` (`app.js:861-881`), after the
`poster_blueprint_state` line and before `promptLoaded = true;`:

```javascript
    document.getElementById("shop_context").value = s.shop_context || "";
    document.getElementById("listing_boilerplate").value = s.listing_boilerplate || "";
    document.getElementById("listing_prompt").value = s.listing_prompt || "";
```

Unlike `prompt_box` and `refine_box`, these three save on the panel's **Save**
button rather than on a debounced `input` listener — `saveSettings` already
posts the whole body, so no extra listener is needed.

Reminder from the Global Constraints: `save_settings` skips empty strings, so
these can be overwritten but not cleared back to empty. That is pre-existing
behaviour and is out of scope here.

- [ ] **Step 6: Cross-check the element ids**

No server. Instead grep the two files against each other and confirm every id
you introduced exists on both sides — a typo here passes every test and
silently breaks the panel:

```bash
grep -o 'getElementById("[a-z_]*")' static/app.js | sort -u
grep -o 'id="[a-z_]*"' static/index.html | sort -u
```

`shop_context`, `listing_boilerplate` and `listing_prompt` must each appear in
both lists. Report the two lists in your report so the reviewer can see them.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add static/app.js static/index.html
git commit -m "feat: edit listing copy in the review card; add listing settings panel"
```

---

## Notes for the implementer

**Not in scope, deliberately.** Size ladders, price maps, bundles, colourways, framing upsells, and rewriting already-published listings. Free shipping, always-on sales and shop sections are Etsy storefront settings, not software.

**Regeneration.** Approving a design that was un-reviewed and re-approved will overwrite its stored copy, including manual edits. That is the intended "regenerate" path for now; no separate button is in this plan.

**No GPU needed.** Every task here is text, HTTP and SQLite. The whole plan is implementable and testable on a Mac with no image generation — the test suite mocks Printify and the Gemini SDK, and never hits the network.
