"""Input parsing, prompt building, and image generation."""

PROMPT_TEMPLATE = (
    "Professional t-shirt graphic design: {phrase}. "
    "{style}Bold, high-contrast, visually striking artwork centered on a plain solid background. "
    "No shirt, no mockup, no watermark - just the artwork itself."
)


def parse_input(text: str) -> list[tuple[str, str]]:
    """Parse pasted 'phrase | filter1, filter2' lines into (phrase, filter) tuples,
    one row per filter term so each term gets its own batch of variations.
    A line with no filters yields a single (phrase, "") row."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        phrase, _, filters = line.partition("|")
        phrase = phrase.strip()
        if not phrase:
            continue
        terms = [f.strip() for f in filters.split(",") if f.strip()]
        for term in terms or [""]:
            items.append((phrase, term))
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


ZIMAGE_MODEL = "Tongyi-MAI/Z-Image-Turbo"
# Z-Image is 6B where FLUX was 12B, so 8-bit fits the card that forced FLUX to 4-bit:
# 7.2GB vs 6.9GB, for near-lossless weights instead of heavily compressed ones.
ZIMAGE_GGUF = (
    "https://huggingface.co/unsloth/Z-Image-Turbo-GGUF/blob/main/z-image-turbo-Q8_0.gguf"
)
# Abliterated Qwen3-4B text encoder: same architecture as the stock one, retrained to
# drop refusals so benign-but-edgy shirt concepts don't get rejected at the prompt stage.
ZIMAGE_ENCODER = "BennyDaBall/Qwen3-4b-Z-Image-Turbo-AbliteratedV1"

_has_local = None


def has_local() -> bool:
    """True when a CUDA/ROCm GPU is available to generate images locally."""
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


def _build_zimage():
    """Z-Image-Turbo with an 8-bit transformer and the abliterated text encoder."""
    import torch
    from diffusers import GGUFQuantizationConfig, ZImagePipeline, ZImageTransformer2DModel
    from transformers import Qwen3Model

    transformer = ZImageTransformer2DModel.from_single_file(
        ZIMAGE_GGUF,
        quantization_config=GGUFQuantizationConfig(compute_dtype=torch.bfloat16),
        torch_dtype=torch.bfloat16,
    )
    # the checkpoint is a ForCausalLM; loading it as the base model drops the unused
    # LM head (~0.8GB) since the pipeline only reads hidden_states[-2]
    text_encoder = Qwen3Model.from_pretrained(ZIMAGE_ENCODER, torch_dtype=torch.bfloat16)
    pipe = ZImagePipeline.from_pretrained(
        ZIMAGE_MODEL,
        transformer=transformer,
        text_encoder=text_encoder,
        torch_dtype=torch.bfloat16,
    )
    pipe.enable_model_cpu_offload()
    if torch.version.hip:
        # Insurance for sizes above 1024, where gfx103x MIOpen has no conv kernel and
        # faults the GPU context outright (FLUX hit this). At 1024 it is a no-op: the
        # latent is 128x128 and tile_latent_min_size is also 128, so it makes one tile.
        # ZImagePipeline has no enable_vae_tiling() wrapper, so drive the VAE directly.
        pipe.vae.enable_tiling()
    return pipe


ZIMAGE_STEPS = 9  # Turbo is distilled for 9 (8 DiT forwards); guidance must stay 0

# name -> (builder, steps). Z-Image replaced FLUX, but the registry stays so the
# stored `image_model` setting keeps meaning something and the next model is a
# one-line addition rather than a rewrite of generate_image_local.
MODELS = {
    "zimage": (_build_zimage, ZIMAGE_STEPS),
}
DEFAULT_MODEL = "zimage"


def current_model() -> str:
    """Which image model the dashboard is set to use."""
    import db

    name = db.get_setting("image_model") or DEFAULT_MODEL
    return name if name in MODELS else DEFAULT_MODEL


FULL_SIZE = 1024
FAST_SIZE = 512  # "Speed Production": a quarter of the pixels to decode


def current_size() -> int:
    """Edge length to generate at. Speed Production trades detail for throughput.

    512 is a quarter of the pixels but only ~2x the speed in practice - measured
    2.7 min vs 5.7 min on an RX 6700 - because model load and text encoding are
    fixed costs that don't shrink with resolution. Anything but an explicit "on"
    means full size.
    """
    import db

    return FAST_SIZE if db.get_setting("speed_production") == "on" else FULL_SIZE


# 50 x 70 cm poster ladder, best quality first. Exact 5:7 (50:70), every side
# divisible by 16 - the latent is /8 with a /2 patch step, so 16 never needs padding.
# probe_poster.py walks this top-down and stops at the first size the GPU survives:
# every rung is taller than 1024, which is where gfx103x MIOpen has no conv kernel.
POSTER_LADDER = (
    (1440, 2016),   # 292 dpi - effectively print standard
    (1280, 1792),   # 260 dpi
    (1120, 1568),   # 227 dpi
    (1040, 1456),   # 211 dpi
    (960, 1344),    # 195 dpi
    (800, 1120),    # 162 dpi - still above the large-format floor
    (720, 1008),    # 146 dpi - below the floor; a diagnostic rung, not a sellable one
)
POSTER_INCHES = (19.685, 27.559)  # 50 x 70 cm


def poster_dpi(width: int, height: int, upscale: int = 4) -> int:
    """Print resolution a generated image gives on a 50x70cm poster, after the 4x
    upscale. Takes the worse of the two axes so an off-ratio image can't flatter
    itself with its long side."""
    w_in, h_in = POSTER_INCHES
    return int(min(width * upscale / w_in, height * upscale / h_in))


def step_progress(step_index: int, steps: int) -> int:
    """Percent to show after finishing step `step_index` (0-based) of `steps`.
    Reserves the top of the bar for the VAE decode that follows the loop."""
    return round((step_index + 1) / (steps + 1) * 100)


_pipe = None
_pipe_name = None


def generate_image_local(prompt: str, on_step=None) -> bytes:
    """Generate one PNG on the local GPU (needs requirements-local.txt).
    Uses whichever model `image_model` names, at whichever size Speed Production
    implies; on_step(pct) gets an int 0-100."""
    global _pipe, _pipe_name
    import io

    name = current_model()
    build, steps = MODELS[name]
    if _pipe_name != name:
        if _pipe is not None:
            # free the outgoing model first - two of these do not fit in 10GB together
            import gc

            import torch

            _pipe, _pipe_name = None, None
            gc.collect()
            torch.cuda.empty_cache()
        _pipe = build()
        _pipe_name = name

    def _cb(pipe, step_index, timestep, kwargs):
        if on_step:
            on_step(step_progress(step_index, steps))
        return kwargs

    size = current_size()  # read per image, so the toggle lands on the next one
    img = _pipe(
        prompt, num_inference_steps=steps, guidance_scale=0.0,
        width=size, height=size, callback_on_step_end=_cb,
    ).images[0]
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
