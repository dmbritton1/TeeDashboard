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
