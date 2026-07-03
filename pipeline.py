"""Input parsing, prompt building, and image generation."""

GEMINI_MODEL = "gemini-2.5-flash-image"

PROMPT_TEMPLATE = (
    "Professional t-shirt graphic design: {phrase}. "
    "{style}Bold, high-contrast, visually striking artwork centered on a plain solid background. "
    "No shirt, no mockup, no watermark - just the artwork itself."
)


def parse_input(text: str) -> list[tuple[str, str]]:
    """Parse pasted 'phrase | filter1, filter2' lines into (phrase, filters) tuples."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        phrase, _, filters = line.partition("|")
        phrase = phrase.strip()
        if not phrase:
            continue
        filters = ", ".join(f.strip() for f in filters.split(",") if f.strip())
        items.append((phrase, filters))
    return items


def build_prompt(phrase: str, filters: str) -> str:
    style = f"Style: {filters}. " if filters else ""
    return PROMPT_TEMPLATE.format(phrase=phrase, style=style)
