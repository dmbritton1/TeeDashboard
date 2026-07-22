"""Input parsing, prompt building, and image generation."""

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


# Curated art-style presets: label -> descriptive keywords appended to the prompt.
# Grouped only for the dropdown; lookup (STYLES) is flat by label.
STYLE_GROUPS = {
    "Flat & graphic": {
        "Screen-print / poster art": "screen-print poster art, bold shapes, limited palette, thick outlines",
        "Vector / flat illustration": "clean vector flat illustration, crisp edges, solid fills",
        "Cel-shaded / graphic-novel": "cel-shaded graphic-novel style, flat color regions, hard shadows",
        "Blockprint / woodcut / linocut": "linocut woodcut blockprint, high-contrast, single-color carving",
        "Engraving / etching / cross-hatch": "vintage engraving and etching, fine cross-hatch line work",
    },
    "Vintage & retro": {
        "WPA / national-park poster": "WPA national-park travel poster, textured shapes over fine detail",
        "Mid-century modern advertising": "1950s-60s mid-century modern advertising, limited palette",
        "Art Deco": "Art Deco, geometric symmetrical ornamental structure",
        "Constructivist / propaganda poster": "constructivist propaganda poster, bold diagonals, 2-3 colors",
        "Pulp / vintage comic": "pulp vintage comic, halftone dots, punchy color",
        "Psychedelic 60s-70s": "psychedelic 1960s-70s, flowing shapes, vivid wild color",
    },
    "Print-texture looks": {
        "Risograph": "risograph print, grain, misregistration, limited spot colors",
        "Halftone / ben-day dots": "halftone ben-day dot comic-print texture",
        "Screenprint distress / grunge": "distressed screenprint with grunge overlay, worn handmade feel",
    },
    "Geometric & abstract": {
        "Bauhaus": "Bauhaus, primary colors, geometric shapes, minimal",
        "Swiss / International Typographic": "Swiss International Typographic style, grid-based, clean",
        "Memphis design": "1980s Memphis design, playful shapes and patterns",
        "Op art": "op art, high-contrast optical patterns",
        "Sacred geometry / line-art mandala": "sacred geometry line-art mandala, symmetrical and detailed",
    },
    "Hand-media looks": {
        "Ink / sumi-e brush": "sumi-e ink brush, loose expressive high-contrast strokes",
        "Papercut / kirigami": "papercut kirigami, layered flat shapes",
        "Collage / mixed-media": "collage mixed-media, textured imperfect layers",
        "Stencil / graffiti": "bold stencil graffiti street-art, one-to-two color",
        "Gig-poster / lowbrow": "gig-poster lowbrow art, dense textured print-ready",
    },
    "Nature & folk": {
        "Botanical illustration": "vintage botanical scientific-plate illustration",
        "Folk art / Scandinavian / Talavera": "folk art Scandinavian Talavera pattern, symmetrical decorative",
        "Ukiyo-e / Japanese woodblock": "ukiyo-e Japanese woodblock, flat color, strong outline",
    },
}
STYLES = {label: desc for group in STYLE_GROUPS.values() for label, desc in group.items()}


def style_filters(style_label: str, filters: str) -> str:
    """Prepend a chosen style preset's keywords to the line's own filters."""
    parts = [p for p in (STYLES.get(style_label, ""), filters) if p]
    return ", ".join(parts)


def build_prompt(phrase: str, filters: str) -> str:
    style = f"Style: {filters}. " if filters else ""
    return PROMPT_TEMPLATE.format(phrase=phrase, style=style)


FLUX_MODEL = "black-forest-labs/FLUX.1-schnell"
# 4-bit transformer: 6.9GB instead of the 24GB bf16 one, so it fits in a 10GB card.
FLUX_GGUF = (
    "https://huggingface.co/unsloth/FLUX.1-schnell-GGUF/blob/main/flux1-schnell-Q4_K_M.gguf"
)

_has_local = None
_flux = None


def has_local() -> bool:
    """True when a CUDA/ROCm GPU is available to run FLUX locally."""
    global _has_local
    if _has_local is None:
        import sys

        if sys.platform == "darwin":
            _has_local = False  # macOS has no CUDA; skip the slow torch import
        else:
            try:
                import torch

                _has_local = torch.cuda.is_available()
            except Exception:
                _has_local = False
    return _has_local


def _build_flux():
    import torch
    from diffusers import FluxPipeline

    if torch.version.hip:
        # AMD: bf16 weights would have to stream layer-by-layer through system RAM,
        # so load a 4-bit transformer that fits in VRAM whole instead.
        from diffusers import FluxTransformer2DModel, GGUFQuantizationConfig

        transformer = FluxTransformer2DModel.from_single_file(
            FLUX_GGUF,
            quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
            torch_dtype=torch.bfloat16,
        )
        pipe = FluxPipeline.from_pretrained(
            FLUX_MODEL, transformer=transformer, torch_dtype=torch.bfloat16
        )
        pipe.enable_model_cpu_offload()
        # MIOpen has no gfx103x kernels for a full-size 1024 decode and faults the GPU
        # context outright; tiling keeps each conv small enough to dodge that path.
        pipe.enable_vae_tiling()
        return pipe

    pipe = FluxPipeline.from_pretrained(FLUX_MODEL, torch_dtype=torch.bfloat16)
    vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
    if vram_gb >= 20:
        pipe.enable_model_cpu_offload()  # whole components in VRAM at once: fast
    else:
        # streams layer-by-layer: fits ~8GB+ cards, slower per image
        pipe.enable_sequential_cpu_offload()
    return pipe


FLUX_STEPS = 4


def step_progress(step_index: int, steps: int) -> int:
    """Percent to show after finishing step `step_index` (0-based) of `steps`.
    Reserves the top of the bar for the VAE decode that follows the loop."""
    return round((step_index + 1) / (steps + 1) * 100)


def generate_image_local(prompt: str, on_step=None) -> bytes:
    """Generate one PNG with FLUX.1-schnell on the local GPU (needs requirements-local.txt).
    on_step(pct) is called after each denoising step with an int 0-100."""
    global _flux
    import io

    if _flux is None:
        _flux = _build_flux()

    def _cb(pipe, step_index, timestep, kwargs):
        if on_step:
            on_step(step_progress(step_index, FLUX_STEPS))
        return kwargs

    img = _flux(
        prompt, num_inference_steps=FLUX_STEPS, guidance_scale=0.0,
        width=1024, height=1024, callback_on_step_end=_cb,
    ).images[0]
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
