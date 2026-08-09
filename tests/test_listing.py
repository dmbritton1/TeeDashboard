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
