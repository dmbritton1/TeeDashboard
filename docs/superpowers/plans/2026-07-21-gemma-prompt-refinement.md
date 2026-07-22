# Gemma Prompt Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Gemma text-model step that rewrites each `phrase | filters` line into N distinct creative image prompts, driven by a user-editable system prompt, before FLUX generates.

**Architecture:** New `refine.py` calls Gemma (`gemma-3-27b-it`) through the already-installed `google-genai` SDK using the existing Gemini key. Refinement runs at queue time in `/api/generate`; each returned prompt is stored in a new nullable `designs.prompt` column, which the worker uses verbatim. The editable system prompt is persisted as the `refine_prompt` setting and surfaced in the Add view with an on/off checkbox. Any Gemma failure or an unchecked box falls back to the existing `build_prompt` template so the queue never breaks.

**Tech Stack:** Python, FastAPI, SQLite, `google-genai`, vanilla JS front-end.

## Global Constraints

- Model: `gemma-4-31b-it`, defined as one constant `GEMMA_MODEL` in `refine.py`.
- Gemma on the Gemini API has **no system role** — the system prompt must be folded into the `contents` string, not passed as `system_instruction`.
- API key comes from `db.get_setting("gemini_api_key")` (which falls back to the `GEMINI_API_KEY` env var via `get_setting`'s upper-case lookup). Never hardcode.
- Refinement failures must **never** block generation: fall back to the existing `pipeline.build_prompt` path and report `refined: false`.
- Follow existing patterns: tests by direct function call using the `load_main` helper (no HTTP client); editable-box save mirrors the existing `prompt_box` debounced-save code.
- Keep files small and single-purpose. No new dependencies.

---

### Task 1: `refine.py` — Gemma refinement module

**Files:**
- Create: `refine.py`
- Test: `tests/test_refine.py`

**Interfaces:**
- Consumes: `db.get_setting` (existing).
- Produces:
  - `GEMMA_MODEL: str = "gemma-3-27b-it"`
  - `DEFAULT_REFINE_PROMPT: str`
  - `refine(phrase: str, filters: str, n: int, system_prompt: str) -> list[str]` — returns up to `n` cleaned prompt strings; raises `RuntimeError`/exception on no key, API error, or unparseable output.
  - `_parse(text: str, n: int) -> list[str]` — strips numbering/bullets/quotes/blank lines, truncates to `n`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_refine.py
import db
import refine


def test_parse_strips_numbering_and_truncates():
    text = "1. a bold fox\n2) neon fox\n- retro fox\n\n"
    assert refine._parse(text, 2) == ["a bold fox", "neon fox"]


def test_parse_strips_wrapping_quotes():
    assert refine._parse('1. "a red dragon"', 1) == ["a red dragon"]


def test_parse_ignores_blank_lines_returns_all_when_fewer_than_n():
    assert refine._parse("only one idea\n\n", 3) == ["only one idea"]


def test_refine_raises_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db.init()
    import pytest
    with pytest.raises(Exception):
        refine.refine("dog dad", "vintage", 2, "system")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_refine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'refine'`

- [ ] **Step 3: Write minimal implementation**

```python
# refine.py
"""Refine phrase + filters into creative image prompts with Gemma (Gemini API)."""
import re

import db

GEMMA_MODEL = "gemma-4-31b-it"

DEFAULT_REFINE_PROMPT = (
    "You are an art director for a print-on-demand t-shirt brand. Given a concept "
    "and optional style keywords, write {n} distinct, vivid image-generation prompts "
    "— each a different creative interpretation. Use the style keywords as creative "
    "direction. Every prompt must describe standalone artwork on a plain solid "
    "background: a t-shirt graphic only — no shirt, no mockup, no watermark, and no "
    "text unless the concept truly needs it. Output only the prompts, numbered 1 to "
    "{n}, one per line."
)

_NUMBERING = re.compile(r"^\s*(?:\d+[.)]|[-*])\s*")


def _parse(text: str, n: int) -> list[str]:
    """Strip numbering/bullets/quotes and blank lines; keep at most n prompts."""
    out = []
    for raw in text.splitlines():
        s = _NUMBERING.sub("", raw.strip()).strip().strip('"').strip()
        if s:
            out.append(s)
    return out[:n]


def refine(phrase: str, filters: str, n: int, system_prompt: str) -> list[str]:
    """One Gemma call -> up to n ready-to-generate image prompts. Raises on failure."""
    from google import genai

    key = db.get_setting("gemini_api_key")
    if not key:
        raise RuntimeError("No Gemini API key configured")
    brief = phrase if not filters else "%s\nStyle keywords: %s" % (phrase, filters)
    # Gemma on the Gemini API has no system role, so fold the system prompt into the content.
    system = system_prompt.replace("{n}", str(n))
    contents = "%s\n\nWrite %d prompts for this concept:\n%s" % (system, n, brief)
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(model=GEMMA_MODEL, contents=contents)
    prompts = _parse(resp.text or "", n)
    if not prompts:
        raise RuntimeError("Gemma returned no usable prompts")
    return prompts


if __name__ == "__main__":
    assert _parse("1. a\n2) b\n- c", 2) == ["a", "b"]
    assert _parse('1. "quoted"', 1) == ["quoted"]
    assert _parse("\n\nlone\n\n", 5) == ["lone"]
    print("refine self-check ok")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_refine.py -v && .venv/bin/python refine.py`
Expected: all tests PASS; prints `refine self-check ok`

- [ ] **Step 5: Commit**

```bash
git add refine.py tests/test_refine.py
git commit -m "feat: refine.py — Gemma prompt refinement module"
```

---

### Task 2: Add `prompt` column to designs

**Files:**
- Modify: `db.py` (the `MIGRATIONS` tuple, around line 32-38)
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: existing `db.init`, `db.MIGRATIONS` pattern.
- Produces: `designs.prompt` nullable TEXT column, present after `db.init()`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_db.py
def test_prompt_column_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    with db.connect() as con:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
    assert "prompt" in cols
```

(If `import db` isn't already at the top of `tests/test_db.py`, add it.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db.py::test_prompt_column_exists -v`
Expected: FAIL — `assert 'prompt' in cols`

- [ ] **Step 3: Add the migration**

In `db.py`, add one entry to the `MIGRATIONS` tuple (after the `test` entry):

```python
    ("test", "ALTER TABLE designs ADD COLUMN test INTEGER NOT NULL DEFAULT 0"),
    ("prompt", "ALTER TABLE designs ADD COLUMN prompt TEXT"),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_db.py::test_prompt_column_exists -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: add nullable prompt column to designs"
```

---

### Task 3: Worker uses stored prompt

**Files:**
- Modify: `worker.py:27` (the prompt-building line in `process_next`)
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `designs.prompt` (Task 2).
- Produces: worker sends `row["prompt"]` verbatim to `generate_image_local` when set.

Note: `tests/test_worker.py` may currently be out of sync with `worker.py` (it references `DAILY_CAP`/`gemini_api_key`). Only add the new test below and run **just that test** by name; do not attempt to green the whole file.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_worker.py
def test_uses_stored_prompt_verbatim(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch, local=True)
    seen = {}
    monkeypatch.setattr(worker.pipeline, "generate_image_local",
                        lambda p: seen.setdefault("prompt", p) or b"PNG")
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, filters, status, prompt) "
                    "VALUES ('dog dad', 'vintage', 'queued', 'a neon dog wizard')")
    worker.process_next()
    assert seen["prompt"] == "a neon dog wizard"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_worker.py::test_uses_stored_prompt_verbatim -v`
Expected: FAIL — `generate_image_local` receives the built template, not the stored prompt (KeyError or assertion mismatch).

- [ ] **Step 3: Update the worker line**

In `worker.py`, replace the existing prompt line inside `process_next` (currently line 27):

```python
        # A refined Gemma prompt (or a raw Test-tab prompt) is used verbatim;
        # otherwise wrap the phrase in the t-shirt template.
        prompt = row["prompt"] or (row["phrase"] if row["test"] else pipeline.build_prompt(row["phrase"], row["filters"]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_worker.py::test_uses_stored_prompt_verbatim -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add worker.py tests/test_worker.py
git commit -m "feat: worker uses stored refined prompt verbatim"
```

---

### Task 4: `/api/generate` refines, `/api/settings` serves the system prompt

**Files:**
- Modify: `main.py` — `GenerateBody` (line 62-65), `SettingsBody` (line 77-82), `generate` (line 84-99), `get_settings` (line 265-270)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `refine.refine`, `refine.DEFAULT_REFINE_PROMPT` (Task 1); `designs.prompt` (Task 2).
- Produces:
  - `POST /api/generate` accepts `refine: bool = True`; returns `{"queued": int, "refined": bool}`.
  - Rows inserted with `prompt` set when refinement succeeded, `NULL` otherwise.
  - `SettingsBody` accepts `refine_prompt: str = ""`; persisted under key `refine_prompt`.
  - `GET /api/settings` returns `refine_prompt` (defaulting to `refine.DEFAULT_REFINE_PROMPT`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_api.py
import refine


def test_generate_stores_refined_prompts(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    monkeypatch.setattr(refine, "refine", lambda ph, fi, n, sp: ["prompt A", "prompt B"][:n])
    res = main.generate(main.GenerateBody(text="dog dad | vintage", variations=2, refine=True))
    assert res == {"queued": 2, "refined": True}
    with db.connect() as con:
        prompts = [r["prompt"] for r in con.execute("SELECT prompt FROM designs ORDER BY id")]
    assert prompts == ["prompt A", "prompt B"]


def test_generate_falls_back_when_gemma_fails(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    def boom(*a, **k):
        raise RuntimeError("no key")
    monkeypatch.setattr(refine, "refine", boom)
    res = main.generate(main.GenerateBody(text="dog dad | vintage", variations=2, refine=True))
    assert res == {"queued": 2, "refined": False}
    with db.connect() as con:
        prompts = [r["prompt"] for r in con.execute("SELECT prompt FROM designs")]
    assert prompts == [None, None]


def test_generate_refine_off_skips_gemma(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    monkeypatch.setattr(refine, "refine", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")))
    res = main.generate(main.GenerateBody(text="dog dad | vintage", variations=1, refine=False))
    assert res == {"queued": 1, "refined": False}


def test_settings_returns_refine_prompt_default(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["refine_prompt"] == refine.DEFAULT_REFINE_PROMPT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -k "refine or refined or gemma" -v`
Expected: FAIL — `GenerateBody` has no `refine` field / `get_settings` has no `refine_prompt` key.

- [ ] **Step 3: Implement the changes in `main.py`**

Add the import near the other imports (top of file, alongside `import worker`):

```python
import refine
```

Extend `GenerateBody`:

```python
class GenerateBody(BaseModel):
    text: str
    variations: int = 2
    style: str = ""
    refine: bool = True
```

Extend `SettingsBody` (add the field):

```python
class SettingsBody(BaseModel):
    printify_api_token: str = ""
    printify_shop_id: str = ""
    access_code: str = ""
    prompt_template: str = ""
    refine_prompt: str = ""
```

Replace the body of `generate` (keep the decorator and signature):

```python
@app.post("/api/generate")
def generate(body: GenerateBody, _gate: None = Depends(require_access_code)):
    items = pipeline.parse_input(body.text)
    if not items:
        raise HTTPException(400, "No valid lines found")
    if _queue_full():
        raise HTTPException(429, "Queue is full - try again shortly")
    system_prompt = db.get_setting("refine_prompt") or refine.DEFAULT_REFINE_PROMPT
    refined_any = False
    queued = 0
    with db.connect() as con:
        for phrase, filters in items:
            filters = pipeline.style_filters(body.style, filters)
            prompts = None
            if body.refine:
                try:
                    prompts = refine.refine(phrase, filters, body.variations, system_prompt)
                except Exception:
                    prompts = None  # any failure -> fall back to the template path below
            if prompts:
                refined_any = True
                for p in prompts:
                    con.execute(
                        "INSERT INTO designs (phrase, filters, prompt, status) VALUES (?, ?, ?, 'queued')",
                        (phrase, filters, p),
                    )
                    queued += 1
            else:
                for _ in range(body.variations):
                    con.execute(
                        "INSERT INTO designs (phrase, filters, status) VALUES (?, ?, 'queued')",
                        (phrase, filters),
                    )
                    queued += 1
    return {"queued": queued, "refined": refined_any}
```

Update `get_settings` to include the refine prompt:

```python
@app.get("/api/settings")
def get_settings():
    keys = ("printify_api_token", "printify_shop_id")
    out = {k: bool(db.get_setting(k)) for k in keys}
    out["prompt_template"] = db.get_setting("prompt_template") or DEFAULT_PROMPT
    out["refine_prompt"] = db.get_setting("refine_prompt") or refine.DEFAULT_REFINE_PROMPT
    return out
```

(No change needed to `save_settings` — it already persists every non-empty field of `SettingsBody`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -k "refine or refined or gemma" -v`
Expected: all four PASS

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_api.py
git commit -m "feat: /api/generate refines prompts with Gemma; settings serve refine_prompt"
```

---

### Task 5: Add view — editable system prompt box + refine toggle

**Files:**
- Modify: `static/index.html` (the `view-add` section, lines 84-101)
- Modify: `static/app.js` (`generate()`, and the prompt-box load/save block near lines 647-664)

**Interfaces:**
- Consumes: `GET/POST /api/settings` `refine_prompt` (Task 4); `POST /api/generate` `refine` field + `refined` response (Task 4).
- Produces: user-visible on/off checkbox and auto-saving system-prompt textarea.

This task is UI wiring with no unit test; verify manually in Step 4.

- [ ] **Step 1: Add the checkbox and collapsible box to `static/index.html`**

Inside the "Commission ideas" panel, add the checkbox to the Generate row and the disclosure after it. Replace the existing generate row (lines 92-95) with:

```html
        <div class="row">
          <button class="gilt" onclick="generate()">Generate designs</button>
          <label style="min-width:0"><input type="checkbox" id="refine_toggle" checked> Refine with Gemma</label>
          <span class="hint">2 variations per line · paced ~2/min to stay inside the free tier</span>
        </div>
        <details class="panel" style="margin-top:12px">
          <summary>Creative refinement (advanced)</summary>
          <div class="panel-label" style="margin-top:8px">System prompt — how Gemma rewrites your lines into creative prompts</div>
          <textarea id="refine_box" spellcheck="false"></textarea>
          <span class="hint">Edits save automatically · leave blank to use the built-in default · <code>{n}</code> is replaced by the variation count</span>
        </details>
```

- [ ] **Step 2: Wire load/save + generate in `static/app.js`**

Extend `loadPrompt()` to also fill the refine box (it already fetches `/api/settings`):

```javascript
async function loadPrompt() {
  try {
    const s = await api("/api/settings");
    document.getElementById("prompt_box").value = s.prompt_template;
    document.getElementById("refine_box").value = s.refine_prompt || "";
    promptLoaded = true;
  } catch (e) {}
}
```

Add a debounced auto-save for the refine box (mirror the existing `prompt_box` listener; place it right after that listener, before `copyPrompt`):

```javascript
let refineSaveTimer;
document.getElementById("refine_box").addEventListener("input", () => {
  if (!promptLoaded) return;
  clearTimeout(refineSaveTimer);
  refineSaveTimer = setTimeout(async () => {
    try {
      await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({refine_prompt: document.getElementById("refine_box").value})});
    } catch (e) { flash("Couldn't save the system prompt — " + e.message); }
  }, 600);
});
```

In `generate()`, send the toggle and surface a skipped-refinement notice. Find the `fetch`/`api` call to `/api/generate` in `generate()` and (a) add `refine: document.getElementById("refine_toggle").checked` to the JSON body, and (b) after a successful response `res`, add:

```javascript
  if (document.getElementById("refine_toggle").checked && res.refined === false)
    flash("Gemma refinement was skipped — generated from the basic template instead.");
```

- [ ] **Step 3: Confirm no syntax errors**

Run: `.venv/bin/pytest -q` (unchanged Python suite still imports/passes for the tasks above)
And check JS by loading the app in Step 4.

- [ ] **Step 4: Manual verification**

Start the app: `.venv/bin/uvicorn main:app --reload` and open `http://127.0.0.1:8000`.
- Add view shows the "Refine with Gemma" checkbox (checked) and the "Creative refinement (advanced)" disclosure; opening it shows the default system prompt text.
- Edit the box, reload the page → your text persists (saved to `refine_prompt`).
- With a valid `GEMINI_API_KEY` set and refine checked, generate `dog dad | vintage` (2 variations) → 2 rows with distinct `prompt` values (check via `sqlite3 designs.db "SELECT prompt FROM designs ORDER BY id DESC LIMIT 2"`).
- Uncheck the box, generate again → new rows have `prompt` NULL.
- Temporarily unset the key (or set an invalid one), generate with refine checked → rows still queue, `prompt` NULL, and the "refinement was skipped" toast appears.

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/app.js
git commit -m "feat: Add-view Gemma refinement toggle and editable system prompt box"
```

---

## Self-Review

**Spec coverage:**
- Model source / Gemma via Gemini key → Task 1 (`GEMMA_MODEL`, key from `db.get_setting`). ✓
- N distinct prompts per variation → Task 1 `refine()` returns list, Task 4 inserts one row each. ✓
- T-shirt rules baked into editable system prompt → `DEFAULT_REFINE_PROMPT` (Task 1), served/persisted (Task 4), editable box (Task 5). ✓
- On/off checkbox default on → Task 4 `refine: bool = True`, Task 5 checkbox. ✓
- Empty-box fallback to default → Task 4 `db.get_setting("refine_prompt") or refine.DEFAULT_REFINE_PROMPT`. ✓
- Error fallback + "skipped" notice → Task 4 try/except + `refined` flag, Task 5 toast. ✓
- New `prompt` column, phrase/filters retained → Task 2 + Task 3. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `refine.refine(phrase, filters, n, system_prompt) -> list[str]` used identically in Tasks 1 and 4. `designs.prompt` column used in Tasks 2/3/4. `{"queued", "refined"}` response used in Task 4 tests and Task 5 JS. ✓
