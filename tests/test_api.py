"""Endpoint tests by direct function call (no HTTP client needed)."""
import importlib
import zipfile

import pytest
from fastapi import HTTPException

import db
import listing
import pipeline
import printify
import worker


def load_main(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(worker, "start", lambda: None)
    import main
    # main starts a background printify.verify() thread at import. It is stubbed
    # for the duration of the reload only - the thread binds its target here, so
    # the stub is what runs, and a test that sets a Printify token can never race
    # that thread into a real network call. printify.verify itself is restored
    # immediately, so tests that call it directly get the real one.
    real_verify = printify.verify
    monkeypatch.setattr(printify, "verify", lambda: (False, "stubbed in tests"))
    main = importlib.reload(main)
    monkeypatch.setattr(printify, "verify", real_verify)
    monkeypatch.setattr(main, "BASE", str(tmp_path))
    return main


def insert(status="pending", **kw):
    row = {"phrase": "dog dad", "filters": "vintage", "status": status, **kw}
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO designs (%s) VALUES (%s)"
            % (", ".join(row), ", ".join("?" * len(row))),
            tuple(row.values()),
        )
        return cur.lastrowid


@pytest.fixture(autouse=True)
def _stub_listing_write(monkeypatch):
    """Default every test to a no-op listing.write so a test calling
    main.approve() without its own stub can't spawn a real daemon thread -
    one that leaks past teardown would write into the developer's real
    designs.db (db.connect() reads db.DB_PATH at call time, after the
    monkeypatch this fixture applies has already been undone). The two tests
    that specifically exercise write() override this in their own body."""
    monkeypatch.setattr(listing, "write", lambda design_id: None)


def test_approve_sets_reviewed_at(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    main.approve(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "approved" and row["reviewed_at"]


def test_unreview_returns_to_pending(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("rejected", reviewed_at="2026-07-01 00:00:00")
    main.unreview(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "pending" and row["reviewed_at"] is None


def test_patch_tags_and_rating(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    main.patch_design(did, main.PatchBody(tags="funny, dog", rating=9))
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["tags"] == "funny, dog"
    assert row["rating"] == 5  # clamped


def test_patch_missing_design_404(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.patch_design(999, main.PatchBody(rating=3))
    assert e.value.status_code == 404


def test_patch_empty_body_400(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending")
    with pytest.raises(HTTPException) as e:
        main.patch_design(did, main.PatchBody())
    assert e.value.status_code == 400


def test_delete_rejected_removes_row_and_files(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    img = tmp_path / "designs" / "9.png"
    img.parent.mkdir(exist_ok=True)
    img.write_bytes(b"png")
    did = insert("rejected", file="designs/9.png")
    main.delete_design(did)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM designs").fetchone()["c"] == 0
    assert not img.exists()


def test_delete_guards_status(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("approved")
    with pytest.raises(HTTPException) as e:
        main.delete_design(did)
    assert e.value.status_code == 409


def test_delete_finished_test_image_bypasses_status_guard(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("pending", test=1)  # a generated scratch image lands in 'pending'
    main.delete_design(did)
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) c FROM designs").fetchone()["c"] == 0


def test_delete_missing_404(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    with pytest.raises(HTTPException) as e:
        main.delete_design(1)
    assert e.value.status_code == 404


def test_publish_stores_product_id(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "t")
    db.set_setting("printify_shop_id", "s")
    pf = tmp_path / "designs" / "7-print.png"
    pf.parent.mkdir(exist_ok=True)
    pf.write_bytes(b"png")
    did = insert("approved", file="designs/7-print.png", print_file="designs/7-print.png")
    monkeypatch.setattr(main.printify, "publish", lambda row: "prod-123")
    main.publish(did)
    with db.connect() as con:
        row = con.execute("SELECT * FROM designs WHERE id = ?", (did,)).fetchone()
    assert row["status"] == "published" and row["product_id"] == "prod-123"


def test_publish_guard_survives_a_listing_failure(tmp_path, monkeypatch):
    """error doubles as the signal upscale uses to release the publish gate
    (main.py's `if not row["print_file"] and ...` check). A listing-copy
    failure also writes to `error`, but must never be mistaken for an
    upscale failure - the governing rule is that a copywriting failure must
    never change what publish does. With print_file still NULL, this must
    still 409 instead of publishing the low-resolution fallback art."""
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "t")
    db.set_setting("printify_shop_id", "s")
    did = insert("approved", error="listing copy failed: quota exhausted")
    with pytest.raises(HTTPException) as e:
        main.publish(did)
    assert e.value.status_code == 409
    assert "upscal" in e.value.detail.lower()


def test_settings_roundtrips_prompt_template(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    out = main.get_settings()
    assert out["prompt_template"] == main.DEFAULT_PROMPT
    main.save_settings(main.SettingsBody(prompt_template="my prompt"))
    assert main.get_settings()["prompt_template"] == "my prompt"


def test_settings_roundtrips_image_model(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    out = main.get_settings()
    assert out["image_model"] == "zimage"        # unset -> default
    assert "zimage" in out["image_models"]
    main.save_settings(main.SettingsBody(image_model="zimage"))
    assert main.get_settings()["image_model"] == "zimage"


def test_unknown_image_model_falls_back_to_default(tmp_path, monkeypatch):
    load_main(tmp_path, monkeypatch)
    import pipeline
    db.set_setting("image_model", "not-a-real-model")
    assert pipeline.current_model() == "zimage"  # never hand the worker a bad name


def test_speed_production_roundtrips_both_ways(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["speed_production"] is False   # default: full size
    assert main.get_settings()["image_size"] == 1024

    main.save_settings(main.SettingsBody(speed_production="on"))
    assert main.get_settings()["speed_production"] is True
    assert main.get_settings()["image_size"] == 512

    # the one that bites: save_settings ignores empty strings, so turning it back
    # off has to send a non-empty "off" or the toggle would be one-way
    main.save_settings(main.SettingsBody(speed_production="off"))
    assert main.get_settings()["speed_production"] is False
    assert main.get_settings()["image_size"] == 1024


def test_flux_is_gone(tmp_path, monkeypatch):
    """FLUX was removed; a stored 'flux' setting must not resurrect it."""
    load_main(tmp_path, monkeypatch)
    import pipeline
    assert "flux" not in pipeline.MODELS
    assert not hasattr(pipeline, "_build_flux")
    db.set_setting("image_model", "flux")        # left over from before the switch
    assert pipeline.current_model() == "zimage"


def test_settings_roundtrips_gemini_key(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["gemini_api_key"] is False
    main.save_settings(main.SettingsBody(gemini_api_key="secret-key"))
    assert main.get_settings()["gemini_api_key"] is True   # reported as a bool, never echoed
    assert db.get_setting("gemini_api_key") == "secret-key"


def test_test_gemini_no_key(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    assert main.test_gemini() == {"ok": False, "message": "No Gemini key saved yet"}


def test_test_gemini_ok(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("gemini_api_key", "k")
    monkeypatch.setattr(main.requests, "get", lambda *a, **kw: FakeResp(200))
    assert main.test_gemini()["ok"] is True


def test_unreview_guards_status(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = insert("queued")
    with pytest.raises(HTTPException) as e:
        main.unreview(did)
    assert e.value.status_code == 409


class FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or []
        self.text = text

    def json(self):
        return self._payload


def test_export_csv(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    insert("published", tags="funny", rating=4, product_id="p1")
    resp = main.export_csv()
    body = resp.body.decode()
    lines = body.strip().splitlines()
    assert lines[0] == "id,phrase,style,status,tags,rating,product_id,created_at"
    assert "dog dad" in lines[1] and "p1" in lines[1]


def test_backup_zip_contains_db_and_images(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    img = tmp_path / "designs" / "1.png"
    img.parent.mkdir(exist_ok=True)
    img.write_bytes(b"png")
    resp = main.backup()
    with zipfile.ZipFile(resp.path) as z:
        names = z.namelist()
    assert "designs.db" in names and "designs/1.png" in names


def test_test_printify_wrong_shop(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "t")
    db.set_setting("printify_shop_id", "42")
    monkeypatch.setattr(main.requests, "get",
                        lambda *a, **kw: FakeResp(200, payload=[{"id": 7, "title": "Other"}]))
    out = main.test_printify()
    assert out["ok"] is False and "42" in out["message"]


def test_verify_stores_success(tmp_path, monkeypatch):
    load_main(tmp_path, monkeypatch)   # for its DB_PATH monkeypatch
    db.set_setting("printify_api_token", "tok")
    db.set_setting("printify_shop_id", "99")
    monkeypatch.setattr(printify.requests, "get",
                        lambda *a, **k: FakeResp(200, payload=[{"id": 99, "title": "S"}]))
    ok, _ = printify.verify()
    assert ok and db.get_setting("printify_verified") == "1"


def test_verify_stores_failure_on_a_dead_token(tmp_path, monkeypatch):
    load_main(tmp_path, monkeypatch)   # for its DB_PATH monkeypatch
    db.set_setting("printify_api_token", "dead")
    db.set_setting("printify_shop_id", "99")
    monkeypatch.setattr(printify.requests, "get",
                        lambda *a, **k: FakeResp(401, payload={}, text="Unauthorized"))
    ok, msg = printify.verify()
    assert not ok and db.get_setting("printify_verified") == "0"
    assert "Unauthorized" in msg


def test_verify_records_nothing_when_the_network_fails(tmp_path, monkeypatch):
    """A connection failure says nothing about the token - only Printify's own
    answer may set the flag. Without this, a dropped wifi would grey out Publish
    and read as 'your token is dead'."""
    load_main(tmp_path, monkeypatch)   # for its DB_PATH monkeypatch
    db.set_setting("printify_api_token", "tok")
    db.set_setting("printify_shop_id", "99")

    def boom(*a, **k):
        raise printify.requests.ConnectionError("no route to host")

    monkeypatch.setattr(printify.requests, "get", boom)
    ok, msg = printify.verify()
    assert not ok and "no route to host" in msg
    assert db.get_setting("printify_verified") is None


def _raises(err):
    def publish(row):
        raise err
    return publish


def _approved_for_publish(tmp_path):
    """An approved row that clears every publish() guard, so a test reaches the
    printify.publish call itself."""
    db.set_setting("printify_api_token", "t")
    db.set_setting("printify_shop_id", "s")
    pf = tmp_path / "designs" / "9-print.png"
    pf.parent.mkdir(exist_ok=True)
    pf.write_bytes(b"png")
    return insert("approved", file="designs/9-print.png", print_file="designs/9-print.png")


def test_publish_clears_the_verified_flag_on_a_401(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    did = _approved_for_publish(tmp_path)
    db.set_setting("printify_verified", "1")
    err = printify.requests.HTTPError("401 Client Error", response=FakeResp(401))
    monkeypatch.setattr(main.printify, "publish", _raises(err))
    with pytest.raises(HTTPException):
        main.publish(did)
    assert db.get_setting("printify_verified") == "0"


def test_publish_keeps_the_verified_flag_on_a_non_401(tmp_path, monkeypatch):
    """A 404 on a URL that happens to contain 401 - a blueprint or shop ID -
    must not demote a working token."""
    main = load_main(tmp_path, monkeypatch)
    did = _approved_for_publish(tmp_path)
    db.set_setting("printify_verified", "1")
    err = printify.requests.HTTPError(
        "404 Client Error for url: https://api.printify.com/v1/catalog/blueprints/401.json",
        response=FakeResp(404))
    monkeypatch.setattr(main.printify, "publish", _raises(err))
    with pytest.raises(HTTPException):
        main.publish(did)
    assert db.get_setting("printify_verified") == "1"


def test_status_tells_unconfigured_from_unverified_from_ready(tmp_path, monkeypatch):
    """Three states, not two. verify() runs at boot and on a settings save, and
    declines to record a verdict when the network is down - so one DNS blip at
    startup leaves a correctly configured shop unverified, and calling that
    'not configured' sends the operator hunting for a token that is already
    there. printify_ready still means verified."""
    main = load_main(tmp_path, monkeypatch)
    assert main.status()["printify_configured"] is False
    assert main.status()["printify_ready"] is False
    db.set_setting("printify_api_token", "tok")
    db.set_setting("printify_shop_id", "99")
    assert main.status()["printify_configured"] is True
    assert main.status()["printify_ready"] is False
    db.set_setting("printify_verified", "1")
    assert main.status()["printify_ready"] is True


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


def test_new_printify_settings_round_trip(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    main.save_settings(main.SettingsBody(tee_colors="Black, Navy",
                                         printify_print_provider_id="9"))
    out = main.get_settings()
    assert out["tee_colors"] == "Black, Navy"
    assert out["printify_print_provider_id"] == "9"


def test_settings_returns_the_stored_tee_colors_not_the_defaults(tmp_path, monkeypatch):
    """Blank means blank. Pre-filling the box with the resolved defaults means the
    next Save freezes today's defaults as an explicit per-shop setting - the same
    trap listing_prompt is deliberately kept out of. The placeholder shows them."""
    main = load_main(tmp_path, monkeypatch)
    assert main.get_settings()["tee_colors"] == ""


def test_tee_colors_stored_cleared_and_untouched(tmp_path, monkeypatch):
    """Three cases, and the third is the one that matters: almost every POST to
    this endpoint carries a single key (the prompt-box autosave fires 600ms after
    any keystroke), and an absent field must leave the setting alone."""
    main = load_main(tmp_path, monkeypatch)
    main.save_settings(main.SettingsBody(tee_colors="Black, Navy"))
    assert printify.tee_colors() == ["Black", "Navy"]
    # absent from the body entirely - what every other save posts
    main.save_settings(main.SettingsBody(prompt_template="anything"))
    assert printify.tee_colors() == ["Black", "Navy"]
    # present and empty - the operator cleared the box
    main.save_settings(main.SettingsBody(tee_colors=""))
    assert printify.tee_colors() == list(printify.DEFAULT_TEE_COLORS)


def test_print_provider_stored_cleared_and_untouched(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    providers = [{"id": 3, "title": "First"}, {"id": 9, "title": "Second"}]
    main.save_settings(main.SettingsBody(printify_print_provider_id="9"))
    assert printify._provider_id(providers) == 9
    main.save_settings(main.SettingsBody(prompt_template="anything"))
    assert printify._provider_id(providers) == 9
    main.save_settings(main.SettingsBody(printify_print_provider_id=""))
    assert printify._provider_id(providers) == 3


def test_settings_roundtrips_the_poster_refine_prompt(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    import refine
    assert main.get_settings()["refine_prompt_poster"] == refine.DEFAULT_REFINE_PROMPT_POSTER
    main.save_settings(main.SettingsBody(refine_prompt_poster="my poster prompt"))
    assert main.get_settings()["refine_prompt_poster"] == "my poster prompt"


def test_generate_hands_gemma_the_posters_system_prompt(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    import refine
    seen = {}

    def fake(phrase, filters, n, system_prompt):
        seen["sp"] = system_prompt
        return ["a refined prompt"]

    monkeypatch.setattr(refine, "refine", fake)
    main.generate(main.GenerateBody(text="a lighthouse", variations=1,
                                    refine=True, product="poster"))
    assert seen["sp"] == refine.DEFAULT_REFINE_PROMPT_POSTER


def test_publish_refuses_a_poster_with_no_blueprint(tmp_path, monkeypatch):
    main = load_main(tmp_path, monkeypatch)
    db.set_setting("printify_api_token", "tok")
    db.set_setting("printify_shop_id", "99")
    did = insert("approved", product="poster", file="designs/1.png",
                 print_file="designs/1_print.png")
    with pytest.raises(HTTPException) as e:
        main.publish(did)
    assert e.value.status_code == 400
    assert "blueprint" in e.value.detail.lower()


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
