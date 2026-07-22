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
