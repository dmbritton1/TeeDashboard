# Listing Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the operator the Etsy listing a design will publish as — editable — before it goes to Printify.

**Architecture:** One function, `printify.listing_fields(design)`, becomes the single source of the listing payload. `publish()` sends it; a new read-only endpoint returns it to the browser; a lightbox panel renders it. The preview therefore cannot disagree with what publishes. Two hardcoded values behind that function (`COLORS`, `providers[0]`) become settings, and `printify_ready` starts meaning "verified" instead of "non-empty".

**Tech Stack:** FastAPI, SQLite via `db.py`, vanilla JS in `static/app.js`, pytest with `requests` and the Gemini SDK mocked. No new dependencies.

## Global Constraints

- **Base branch is `claude/dashboard-setup-578b0f` (PR #1), not `main`.** Every task assumes `listing.py`, the `listing_title` / `listing_tags` / `listing_hook` columns, and the delegated blur-save handler already exist.
- Etsy caps, already defined in `listing.py` and **not to be duplicated**: `TITLE_MAX = 140`, `TAG_MAX = 20`, `TAG_COUNT = 13`. Import them; never re-type the numbers.
- `listing.clamp_title` and `listing.clean_tags` are the single enforcement point. Anything that writes a title or tags routes through them.
- No test may touch the network. Follow the existing harnesses: `tests/test_printify.py` monkeypatches `requests`, `tests/test_api.py` calls endpoint functions directly with `load_main`.
- Never write to `designs.error` outside upscale's own path — `main.publish` reads that column as upscale's completion signal.
- Python style in this repo: `%`-formatting, not f-strings. Match it.
- The stored Printify token is dead; every authenticated endpoint 401s. Nothing here can be verified against a live shop. All tests mock.

---

### Task 0: Rebase onto PR #1

**Files:**
- Modify: none (git only)

**Interfaces:**
- Consumes: nothing
- Produces: a working tree containing `listing.py` and PR #1's schema, on which every later task depends

- [ ] **Step 1: Fetch the base branch**

```bash
git fetch origin claude/dashboard-setup-578b0f
```

- [ ] **Step 2: Rebase this branch onto it**

```bash
git rebase origin/claude/dashboard-setup-578b0f
```

- [ ] **Step 3: Confirm PR #1's code is present**

Run: `ls listing.py && git log --oneline -3`
Expected: `listing.py` exists; the log shows PR #1's commits beneath the spec commits.

- [ ] **Step 4: Confirm the existing suite is green before changing anything**

Run: `.venv/bin/pytest -q`
Expected: 161 passed, 1 skipped. If this does not pass, stop — do not build on a red baseline.

---

### Task 1: `listing_fields` — one source for the listing payload

**Files:**
- Modify: `printify.py` (add `listing_fields`, rewrite the `_post("/shops/%s/products.json")` payload to use it)
- Test: `tests/test_printify.py`

**Interfaces:**
- Consumes: `listing.clean_tags`, `pipeline.product_data`, `printify._description` (all exist from PR #1)
- Produces:
  ```python
  printify.listing_fields(design: dict) -> dict
  # {"title": str, "description": str, "tags": list[str],
  #  "price_cents": int, "colors": list[str], "product_label": str}
  ```
  Never raises for an unconfigured product. Blueprint and print-provider resolution stay in `publish()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_printify.py`:

```python
def test_listing_fields_prefers_the_generated_title(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    fields = printify.listing_fields(
        {"phrase": "dog dad", "product": "tee", "listing_title": "Dog Dad Tee, Funny Gift"}
    )
    assert fields["title"] == "Dog Dad Tee, Funny Gift"


def test_listing_fields_falls_back_to_the_old_title(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    fields = printify.listing_fields({"phrase": "dog dad", "product": "tee"})
    assert fields["title"] == "Dog Dad T-Shirt"


def test_listing_fields_cleans_tags(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    fields = printify.listing_fields(
        {"phrase": "p", "product": "tee", "listing_tags": "A, a, %s" % ("x" * 21)}
    )
    assert fields["tags"] == ["a"]


def test_listing_fields_does_not_raise_without_a_blueprint(tmp_path, monkeypatch):
    # a poster has no blueprint configured on this account; the preview must
    # still render rather than 500
    setup_tmp(tmp_path, monkeypatch)
    fields = printify.listing_fields({"phrase": "p", "product": "poster"})
    assert fields["product_label"] == "Poster (50x70cm)"
    assert fields["price_cents"] == 3499


def test_publish_sends_exactly_listing_fields(tmp_path, monkeypatch):
    """The anti-drift test: whatever listing_fields says is what Printify gets."""
    setup_tmp(tmp_path, monkeypatch)
    sent = {}

    def fake_post(path, payload, timeout=60):
        if path.endswith("/products.json"):
            sent.update(payload)
            return {"id": "prod1"}
        return {"id": "img1"}

    monkeypatch.setattr(printify, "_post", fake_post)
    monkeypatch.setattr(printify, "_get", lambda path: (
        [{"id": 7}] if "print_providers.json" in path else {"variants": TEE_VARIANTS}))
    png = tmp_path / "d.png"
    png.write_bytes(b"x")
    design = {"id": 1, "phrase": "dog dad", "product": "tee", "file": str(png),
              "listing_title": "Dog Dad Tee", "listing_tags": "dog dad, funny",
              "listing_hook": "A hook."}

    printify.publish(design)

    fields = printify.listing_fields(design)
    assert sent["title"] == fields["title"]
    assert sent["description"] == fields["description"]
    assert sent["tags"] == fields["tags"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_printify.py -q -k listing_fields`
Expected: FAIL with `AttributeError: module 'printify' has no attribute 'listing_fields'`

- [ ] **Step 3: Implement `listing_fields`**

Add to `printify.py`, directly beneath `_description`:

```python
def listing_fields(design: dict) -> dict:
    """Everything about a listing that is decided before we talk to Printify.

    Both the publish payload and the preview endpoint read this, so the
    operator cannot be shown a listing that differs from the one that ships.
    Deliberately excludes blueprint and print provider: those need the network,
    and _blueprint() raises for a product with none configured, which would
    turn a preview of an unconfigured poster into a 500.
    """
    product = design.get("product") or pipeline.DEFAULT_PRODUCT
    data = pipeline.product_data(product)
    return {
        "title": design.get("listing_title")
                 or design["phrase"].title() + " " + data["title_suffix"],
        "description": _description(design),
        "tags": listing.clean_tags(design.get("listing_tags") or ""),
        "price_cents": data["price_cents"],
        "colors": sorted(COLORS) if product == "tee" else [],
        "product_label": data["label"],
    }
```

- [ ] **Step 4: Rewrite the publish payload to consume it**

In `printify.publish()`, replace the `title` / `description` / `tags` lines of the products POST with:

```python
    fields = listing_fields(design)
    product_json = _post(
        "/shops/%s/products.json" % shop_id,
        {
            "title": fields["title"],
            "description": fields["description"],
            "tags": fields["tags"],
            "blueprint_id": blueprint_id,
            "print_provider_id": pp_id,
```

Leave the rest of the payload untouched.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all previous tests still pass, plus the five new ones.

- [ ] **Step 6: Commit**

```bash
git add printify.py tests/test_printify.py
git commit -m "refactor: one source for the listing payload

listing_fields() is what publish sends, so a preview reading it cannot drift
from what actually ships."
```

---

### Task 2: Colours become a setting

**Files:**
- Modify: `printify.py:11` (`COLORS`), `_select_variants`, `listing_fields`
- Test: `tests/test_printify.py`

**Interfaces:**
- Consumes: `printify.listing_fields` (Task 1)
- Produces:
  ```python
  printify.DEFAULT_TEE_COLORS: tuple[str, ...]
  printify.tee_colors() -> list[str]
  printify._select_variants(product: str, variants: list, colors: list[str]) -> list
  ```
  Note the **new third parameter** on `_select_variants`. Existing tests call it with two and must be updated.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_printify.py`:

```python
def test_tee_colors_default_when_unset(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert printify.tee_colors() == list(printify.DEFAULT_TEE_COLORS)


def test_tee_colors_parse_and_strip(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("tee_colors", " Black , , Navy ")
    assert printify.tee_colors() == ["Black", "Navy"]


def test_blank_tee_colors_setting_falls_back(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("tee_colors", "   ,  ")
    assert printify.tee_colors() == list(printify.DEFAULT_TEE_COLORS)


def test_select_variants_uses_the_colours_passed_in():
    picked = printify._select_variants("tee", TEE_VARIANTS, ["Neon Pink"])
    assert [v["id"] for v in picked] == [2]


def test_listing_fields_reports_the_configured_colours(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("tee_colors", "Black, Navy")
    assert printify.listing_fields({"phrase": "p", "product": "tee"})["colors"] == ["Black", "Navy"]


def test_listing_fields_has_no_colours_for_a_poster(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert printify.listing_fields({"phrase": "p", "product": "poster"})["colors"] == []
```

- [ ] **Step 2: Update the three existing `_select_variants` tee tests to pass colours**

In `tests/test_printify.py`, change:

```python
def test_tee_variants_are_filtered_to_the_stocked_colours():
    assert [v["id"] for v in printify._select_variants(
        "tee", TEE_VARIANTS, ["Black", "White"])] == [1, 3]
```

and in `test_unrecognised_catalogue_falls_back_rather_than_publishing_nothing`, change the tee assertion to:

```python
    assert printify._select_variants(
        "tee", [{"id": 40, "options": {"color": "Lime"}}], ["Black", "White"]
    ) == [{"id": 40, "options": {"color": "Lime"}}]
```

The two poster tests take the new argument too — pass `[]`, since the poster branch ignores it.

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest tests/test_printify.py -q`
Expected: FAIL — `tee_colors` undefined, and `_select_variants() takes 2 positional arguments but 3 were given`.

- [ ] **Step 4: Implement**

In `printify.py`, replace `COLORS = {"Black", "White"}` with:

```python
# Printify renders mockups per enabled colour, so a narrow list is a direct
# cause of a thin mockup set on the listing.
DEFAULT_TEE_COLORS = ("Black", "White", "Navy", "Sport Grey", "Sand")


def tee_colors() -> list[str]:
    """Colours to enable on a tee. Names must match the print provider's
    catalogue exactly; anything it doesn't recognise hits _select_variants'
    existing fallback rather than publishing nothing."""
    raw = db.get_setting("tee_colors") or ""
    return [c.strip() for c in raw.split(",") if c.strip()] or list(DEFAULT_TEE_COLORS)
```

Change `_select_variants` to take colours rather than read them, keeping it a pure function so tests need no database:

```python
def _select_variants(product: str, variants: list, colors: list) -> list:
```

and its final line to:

```python
    return [v for v in variants if v["options"].get("color") in set(colors)] or variants[:10]
```

In `listing_fields`, replace `sorted(COLORS)` with `tee_colors()`.

In `publish()`, pass them through:

```python
    variants = _select_variants(product, all_variants, tee_colors())
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. `grep -n COLORS printify.py` should show no surviving reference to the old constant.

- [ ] **Step 6: Commit**

```bash
git add printify.py tests/test_printify.py
git commit -m "feat: tee colours become a setting

Black and White alone is a direct cause of a thin Printify mockup set."
```

---

### Task 3: Print provider becomes a setting

**Files:**
- Modify: `printify.py` (`publish`, add `_provider_id`)
- Test: `tests/test_printify.py`

**Interfaces:**
- Consumes: `db.get_setting`
- Produces:
  ```python
  printify._provider_id(providers: list) -> int
  ```

- [ ] **Step 1: Write the failing tests**

```python
PROVIDERS = [{"id": 3, "title": "First"}, {"id": 9, "title": "Second"}]


def test_provider_defaults_to_the_first(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    assert printify._provider_id(PROVIDERS) == 3


def test_provider_honours_the_setting(tmp_path, monkeypatch):
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("printify_print_provider_id", "9")
    assert printify._provider_id(PROVIDERS) == 9


def test_unknown_provider_setting_falls_back_rather_than_failing(tmp_path, monkeypatch):
    # same habit as _select_variants: publish narrow, never publish nothing
    setup_tmp(tmp_path, monkeypatch)
    db.set_setting("printify_print_provider_id", "404")
    assert printify._provider_id(PROVIDERS) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_printify.py -q -k provider`
Expected: FAIL with `AttributeError: module 'printify' has no attribute '_provider_id'`

- [ ] **Step 3: Implement**

Add to `printify.py`:

```python
def _provider_id(providers: list) -> int:
    """The configured print provider, or the first one. Providers differ in
    both price and mockup library, so which one you get should not be an
    accident of catalogue ordering - but an unknown id falls back rather than
    raising, same habit as _select_variants."""
    want = db.get_setting("printify_print_provider_id")
    if want:
        for p in providers:
            if str(p["id"]) == str(want):
                return int(p["id"])
    return providers[0]["id"]
```

In `publish()`, replace `pp_id = providers[0]["id"]` with `pp_id = _provider_id(providers)`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add printify.py tests/test_printify.py
git commit -m "feat: print provider becomes a setting

providers[0] was a coin flip, and providers differ in mockup library and price."
```

---

### Task 4: `GET /api/designs/{id}/listing`

**Files:**
- Modify: `main.py` (new endpoint after `patch_design`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `printify.listing_fields` (Task 1)
- Produces: `GET /api/designs/{design_id}/listing` → the `listing_fields` dict. 404 when the design does not exist. Available for any status, so a pending design can be previewed before approval.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api.py`:

```python
def test_listing_endpoint_returns_the_publish_payload(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("approved", listing_title="Dog Dad Tee", listing_tags="dog dad, funny",
                 listing_hook="A hook.")
    out = main.design_listing(did)
    assert out["title"] == "Dog Dad Tee"
    assert out["tags"] == ["dog dad", "funny"]
    assert "A hook." in out["description"]
    assert out["price_cents"] == 2499


def test_listing_endpoint_includes_the_boilerplate(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("listing_boilerplate", "Printed on demand.")
    did = insert("approved", listing_hook="A hook.")
    assert main.design_listing(did)["description"] == "A hook.\n\nPrinted on demand."


def test_listing_endpoint_404s_on_a_missing_design(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.design_listing(9999)
    assert e.value.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_api.py -q -k listing_endpoint`
Expected: FAIL with `AttributeError: module 'main' has no attribute 'design_listing'`

- [ ] **Step 3: Implement**

Add to `main.py`, immediately after `patch_design`:

```python
@app.get("/api/designs/{design_id}/listing")
def design_listing(design_id: int):
    """The listing exactly as publish would send it. Read-only, and the same
    function publish uses, so the preview cannot show something else."""
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (design_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Design not found")
    return printify.listing_fields(dict(row))
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_api.py
git commit -m "feat: read-only endpoint returning the listing publish would send"
```

---

### Task 5: `printify_ready` means verified

**Files:**
- Modify: `printify.py` (add `verify`), `main.py` (`test_printify`, `save_settings`, `status`, `publish`'s failure branch, startup)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: nothing from earlier tasks
- Produces:
  ```python
  printify.verify() -> tuple[bool, str]   # (ok, human message); stores "printify_verified" = "1"/"0"
  ```
  `main.status()["printify_ready"]` becomes `token and shop and printify_verified == "1"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_verify_stores_success(tmp_path, monkeypatch):
    load_main(tmp_path, monkeypatch)   # for its DB_PATH monkeypatch
    db.set_setting("printify_api_token", "tok")
    db.set_setting("printify_shop_id", "99")
    monkeypatch.setattr(printify.requests, "get", lambda *a, **k: _resp(200, [{"id": 99, "title": "S"}]))
    ok, _ = printify.verify()
    assert ok and db.get_setting("printify_verified") == "1"


def test_verify_stores_failure_on_a_dead_token(tmp_path, monkeypatch):
    load_main(tmp_path, monkeypatch)   # for its DB_PATH monkeypatch
    db.set_setting("printify_api_token", "dead")
    db.set_setting("printify_shop_id", "99")
    monkeypatch.setattr(printify.requests, "get", lambda *a, **k: _resp(401, {}, "Unauthorized"))
    ok, msg = printify.verify()
    assert not ok and db.get_setting("printify_verified") == "0"
    assert "Unauthorized" in msg


def test_status_is_not_ready_until_verified(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "tok")
    db.set_setting("printify_shop_id", "99")
    assert main.status()["printify_ready"] is False
    db.set_setting("printify_verified", "1")
    assert main.status()["printify_ready"] is True
```

Add this helper near the top of `tests/test_api.py`:

```python
class _resp:
    """Minimal stand-in for a requests.Response."""
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or str(payload)

    def json(self):
        return self._payload
```

and `import printify` alongside the existing imports.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_api.py -q -k "verify or not_ready"`
Expected: FAIL — `printify.verify` undefined.

- [ ] **Step 3: Move the check into `printify.verify`**

Add to `printify.py`:

```python
def verify() -> tuple[bool, str]:
    """Ask Printify whether this token and shop actually work, and remember the
    answer. The dashboard polls /api/status every three seconds, so the answer
    is stored rather than re-fetched - a network call in that path would put a
    multi-second hang between the operator and their own dashboard."""
    token = db.get_setting("printify_api_token")
    shop = db.get_setting("printify_shop_id")
    if not (token and shop):
        return False, "Save a Printify token and shop ID first"
    try:
        r = requests.get(API + "/shops.json",
                         headers={"Authorization": "Bearer %s" % token}, timeout=15)
    except Exception as e:
        # a network failure is not evidence the token is bad, so don't record one
        return False, "Couldn't reach Printify: %s" % e
    if r.status_code != 200:
        db.set_setting("printify_verified", "0")
        return False, "Printify says: %s" % r.text[:300]
    shops = r.json()
    if any(str(s.get("id")) == str(shop) for s in shops):
        db.set_setting("printify_verified", "1")
        return True, "Printify connected"
    db.set_setting("printify_verified", "0")
    names = ", ".join("%s (%s)" % (s.get("title"), s.get("id")) for s in shops) or "none"
    return False, "Token works, but shop %s isn't on this account. Your shops: %s" % (shop, names)
```

- [ ] **Step 4: Rewire `main.py`**

Replace the body of `test_printify()` with:

```python
@app.post("/api/test/printify")
def test_printify():
    ok, message = printify.verify()
    return {"ok": ok, "message": message}
```

In `status()`, change the `printify_ready` line to:

```python
        "printify_ready": bool(
            db.get_setting("printify_api_token")
            and db.get_setting("printify_shop_id")
            and db.get_setting("printify_verified") == "1"
        ),
```

At the end of `save_settings`, before the return:

```python
    if body.printify_api_token.strip() or body.printify_shop_id.strip():
        threading.Thread(target=printify.verify, daemon=True).start()
```

In `publish()`'s `except` branch, after storing the error message:

```python
        if "401" in str(e):
            db.set_setting("printify_verified", "0")
```

Near `worker.start()` at module level, so the flag is populated within seconds of boot rather than staying false until the operator saves something:

```python
threading.Thread(target=printify.verify, daemon=True).start()
```

Add `import threading` to `main.py`'s imports.

**Test safety:** `load_main` reloads `main`, so this thread starts in every API
test. It is harmless because `verify()` returns before touching the network
when either the token or the shop ID is missing, and `load_main` runs against a
fresh empty database. Any future test that sets a Printify token *before*
calling `load_main` must monkeypatch `printify.verify` — otherwise the suite
makes a real network call.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. Existing `test_printify` endpoint tests should still pass unchanged — the function's return shape is identical.

- [ ] **Step 6: Commit**

```bash
git add printify.py main.py tests/test_api.py
git commit -m "fix: printify_ready means verified, not merely non-empty

A dead token showed as '✓ Printify connected' because the check only asked
whether a token existed. Verified on save, at startup and on a 401 - never in
the three-second poll."
```

---

### Task 6: The preview panel

**Files:**
- Modify: `static/app.js` (`card`, delete `listingBox`, add `openPreview` / `renderPreview`, guard the arrow keys), `static/styles.css`
- Test: browser verification (this repo has no JS test harness; do not add one)

**Interfaces:**
- Consumes: `GET /api/designs/{id}/listing` (Task 4), the delegated `blur` handler and `PATCH /api/designs/{id}` from PR #1
- Produces: `openPreview(id)` — global, called from the card button

- [ ] **Step 1: Replace the accordion with a button**

In `static/app.js`, delete the whole `listingBox` function and its call in `card()`. In `card()`'s `buttons.approved`, prepend:

```js
    (d.listing_title || d.listing_hook
      ? `<button onclick="openPreview(${d.id})">👁 Preview listing</button>`
      : `<button disabled>writing copy…</button>`) +
```

- [ ] **Step 2: Add the preview renderer**

Add near `renderLightbox` in `static/app.js`:

```js
// The preview reuses the lightbox shell but not its arrow-key navigation:
// stepping to the next design out from under a half-typed title would lose it.
let lbMode = "image";
function openPreview(id) { lbId = id; lbMode = "listing"; renderPreview(); }

async function renderPreview() {
  const d = findDesign(lbId);
  if (!d) { closeLightbox(); return; }
  let f;
  try {
    f = await api(`/api/designs/${d.id}/listing`);
  } catch (e) { flash("Couldn't load the listing — " + e.message); return; }
  const tags = f.tags.map(t => `<span class="tag">${esc(t)}</span>`).join(" ");
  const price = "$" + (f.price_cents / 100).toFixed(2);
  document.getElementById("lightbox_inner").innerHTML =
    `<div>${d.file ? `<img src="/${d.file}" alt="${esc(d.phrase)}">` : `<div class="placeholder">no image</div>`}</div>` +
    `<div class="preview"><h3>Listing preview</h3>` +
    `<div class="lb-row">${esc(f.product_label)} · ${price}` +
      (f.colors.length ? ` · ${f.colors.length} colours` : "") + `</div>` +
    `<label>Title <span class="count ${f.title.length > 140 ? "over" : ""}">${f.title.length}/140</span></label>` +
    `<textarea data-f="listing_title" data-id="${d.id}" rows="3">${esc(f.title)}</textarea>` +
    `<label>Tags <span class="count">${f.tags.length}/13</span></label>` +
    `<div class="chips">${tags || "<em>none yet</em>"}</div>` +
    `<textarea data-f="listing_tags" data-id="${d.id}" rows="2">${esc(f.tags.join(", "))}</textarea>` +
    `<label>Description <span class="hint">hook plus your boilerplate, as Printify receives it</span></label>` +
    `<div class="lb-row desc">${esc(f.description)}</div>` +
    `<label>Hook (editable)</label>` +
    `<textarea data-f="listing_hook" data-id="${d.id}" rows="4">${esc(d.listing_hook || "")}</textarea>` +
    `<div class="lb-row"><button onclick="closeLightbox()">Close</button></div></div>`;
  document.getElementById("lightbox").hidden = false;
}
```

- [ ] **Step 3: Make the shared close and key handlers mode-aware**

Change `closeLightbox` and the keydown listener in `static/app.js`:

```js
function closeLightbox() {
  lbId = null; lbMode = "image";
  document.getElementById("lightbox").hidden = true;
}
```

and inside the existing `document.addEventListener("keydown", ...)` for the lightbox, guard the arrows:

```js
  if (ev.key === "Escape") closeLightbox();
  if (lbMode === "listing") return;
  if (ev.key === "ArrowRight") lbMove(1);
  if (ev.key === "ArrowLeft") lbMove(-1);
```

Also set `lbMode = "image"` inside `openLightbox`.

- [ ] **Step 4: Re-render the preview after a field is saved**

The delegated blur handler PR #1 added calls `refresh()`. Extend its success branch so an open preview reflects the newly-assembled description:

```js
    refresh();
    if (lbMode === "listing") renderPreview();
```

- [ ] **Step 5: Style it**

Add to `static/styles.css`:

```css
.preview label { display:block; margin:10px 0 4px; font:12px var(--mono); color:var(--muted); }
.preview textarea { width:100%; }
.preview .count { float:right; }
.preview .count.over { color:var(--clay); }
.preview .chips { display:flex; flex-wrap:wrap; gap:4px; margin-bottom:6px; }
.preview .desc { white-space:pre-wrap; }
```

- [ ] **Step 6: Verify in the browser**

Start the server, approve a design, open the preview, and confirm each of these:

- The title counter matches the character count, and turns `.over` past 140.
- Typing a 21-character tag and blurring drops it — the chips row re-renders without it.
- Typing a duplicate tag in different cases keeps one, lower-cased.
- Editing the hook and blurring updates the assembled description above it.
- A field being typed in survives a three-second poll (PR #1's focus guard).
- Arrow keys do nothing while the preview is open; Escape closes it.
- The card shows `writing copy…` disabled for a design approved seconds ago, and the button once copy lands.

- [ ] **Step 7: Commit**

```bash
git add static/app.js static/styles.css
git commit -m "feat: listing preview replaces the copy accordion

One editing surface rather than two, laid out as the listing rather than as a
form, reading the same payload publish sends."
```

---

### Task 7: Settings UI for the two new values

**Files:**
- Modify: `static/index.html` (Settings tab), `static/app.js` (`saveSettings`, `loadPrompt`), `main.py` (`SettingsBody`, `get_settings`)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `printify.tee_colors` (Task 2), `printify._provider_id` (Task 3)
- Produces: `tee_colors` and `printify_print_provider_id` round-trip through `/api/settings`

- [ ] **Step 1: Write the failing test**

```python
def test_new_printify_settings_round_trip(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    main.save_settings(main.SettingsBody(tee_colors="Black, Navy",
                                         printify_print_provider_id="9"))
    out = main.get_settings()
    assert out["tee_colors"] == "Black, Navy"
    assert out["printify_print_provider_id"] == "9"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_api.py -q -k new_printify_settings`
Expected: FAIL — `SettingsBody` has no such fields.

- [ ] **Step 3: Implement the server side**

In `main.py`'s `SettingsBody`, add:

```python
    tee_colors: str = ""
    printify_print_provider_id: str = ""
```

In `get_settings()`, add:

```python
    out["tee_colors"] = ", ".join(printify.tee_colors())
    out["printify_print_provider_id"] = db.get_setting("printify_print_provider_id") or ""
```

- [ ] **Step 4: Add the fields to the Settings tab**

In `static/index.html`, beside the existing Printify inputs:

```html
<label>T-shirt colours
  <input type="text" id="tee_colors" placeholder="Black, White, Navy, Sport Grey, Sand">
  <span class="hint">Names must match your print provider's catalogue. More colours means more Printify mockups.</span>
</label>
<label>Print provider ID
  <input type="text" id="printify_provider" placeholder="leave blank for the first one">
</label>
```

In `static/app.js`'s `saveSettings` body object add:

```js
    tee_colors: document.getElementById("tee_colors").value,
    printify_print_provider_id: document.getElementById("printify_provider").value,
```

and in `loadPrompt`, beside the other assignments:

```js
    document.getElementById("tee_colors").value = s.tee_colors || "";
    document.getElementById("printify_provider").value = s.printify_print_provider_id || "";
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: all pass.

- [ ] **Step 6: Verify the round-trip in the browser**

Save both fields, reload the page, confirm they come back populated, and confirm a blank colours field falls back to the five defaults rather than saving empty.

- [ ] **Step 7: Commit**

```bash
git add main.py static/index.html static/app.js tests/test_api.py
git commit -m "feat: settings for tee colours and print provider"
```

---

## Done when

- `.venv/bin/pytest -q` is green, above PR #1's 161 passed / 1 skipped.
- `grep -n 'COLORS\|providers\[0\]' printify.py` returns nothing.
- The approved card offers a preview, not an accordion, and the preview's fields save.
- A dead Printify token shows as not connected on the dashboard.
