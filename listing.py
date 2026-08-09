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
