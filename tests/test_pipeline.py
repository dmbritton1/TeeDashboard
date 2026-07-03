from pipeline import parse_input, build_prompt


def test_parse_basic():
    text = "funny fishing shirt | vintage, distressed, black shirt\nplant mom | retro 70s, floral\n"
    assert parse_input(text) == [
        ("funny fishing shirt", "vintage, distressed, black shirt"),
        ("plant mom", "retro 70s, floral"),
    ]


def test_parse_bare_phrase_and_blank_lines():
    assert parse_input("\ndog dad\n\n") == [("dog dad", "")]


def test_parse_strips_messy_whitespace():
    assert parse_input("  cat mom  |  cute ,  pastel  ") == [("cat mom", "cute, pastel")]


def test_parse_skips_empty_phrase():
    assert parse_input("| vintage") == []


def test_prompt_includes_phrase_and_filters():
    p = build_prompt("dog dad", "minimalist, line art")
    assert "dog dad" in p and "minimalist, line art" in p


def test_prompt_without_filters_has_no_style_clause():
    assert "Style:" not in build_prompt("dog dad", "")
