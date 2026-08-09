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


def test_poster_refine_prompt_is_wall_art_not_garment():
    p = refine.DEFAULT_REFINE_PROMPT_POSTER.lower()
    assert "t-shirt" not in p and "shirt" not in p
    assert "5:7" in p


def test_refine_defaults_cover_every_settings_key():
    assert refine.DEFAULTS["refine_prompt"] is refine.DEFAULT_REFINE_PROMPT
    assert refine.DEFAULTS["refine_prompt_poster"] is refine.DEFAULT_REFINE_PROMPT_POSTER
