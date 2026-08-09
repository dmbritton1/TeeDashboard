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
