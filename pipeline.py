"""Input parsing, prompt building, and image generation."""

PROMPT_TEMPLATE = (
    "Professional t-shirt graphic design: {phrase}. "
    "{style}Bold, high-contrast, visually striking artwork centered on a plain solid background. "
    "No shirt, no mockup, no watermark - just the artwork itself."
)

# Unlike PROMPT_TEMPLATE this states what it wants rather than what it doesn't.
# Z-Image-Turbo runs at guidance_scale=0.0, so with no classifier-free guidance
# negative phrasing barely binds - the size probe's prompt said "no frame, no
# border" and the image came out with a prominent frame.
POSTER_TEMPLATE = (
    "Fine-art poster print: {phrase}. "
    "{style}One clear focal subject, upright vertical composition, "
    "artwork running edge to edge and filling the whole canvas, "
    "clear foreground and distant background."
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


def build_prompt(phrase: str, filters: str, product: str = "tee") -> str:
    """Wrap a phrase in its product's template. The default keeps every caller
    that predates posters producing exactly the prompt it produced before."""
    style = f"Style: {filters}. " if filters else ""
    return product_data(product)["template"].format(phrase=phrase, style=style)


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


ATTENTION_CHUNK = 1024        # query rows per block
ATTENTION_CHUNK_ABOVE = 4096  # 1024x1024's token count - the size that already works


def _chunked_sdpa(sdpa):
    """Wrap torch's attention so long sequences compute in query blocks.

    gfx1031 has neither the flash nor the memory-efficient SDPA kernel ("No available
    kernel"), so torch falls back to MATH, which materialises the whole NxN score
    matrix: measured 26.6GB at 1440x2016 against a 10GB card. Each query row's
    softmax depends only on its own row, so slicing the queries is exact rather than
    an approximation - it trades a few more kernel launches for O(N x chunk) memory.
    Measured 26.6GB -> 2.9GB at that size.

    At or below ATTENTION_CHUNK_ABOVE it calls straight through, so square t-shirt
    generation keeps running on precisely the path it always has.
    """
    import torch

    def wrapper(query, key, value, attn_mask=None, dropout_p=0.0, is_causal=False,
                scale=None, enable_gqa=False, **kwargs):
        # is_causal masks by absolute position, which a block can't know about
        if is_causal or query.shape[-2] <= ATTENTION_CHUNK_ABOVE:
            return sdpa(query, key, value, attn_mask=attn_mask, dropout_p=dropout_p,
                        is_causal=is_causal, scale=scale, enable_gqa=enable_gqa, **kwargs)
        # a mask with a real query dimension has to be cut to match its block; one
        # that broadcasts (size 1 there) applies to every block as-is
        sliceable = attn_mask is not None and attn_mask.ndim >= 2 and attn_mask.shape[-2] != 1
        out = torch.empty_like(query)
        for i in range(0, query.shape[-2], ATTENTION_CHUNK):
            block = slice(i, i + ATTENTION_CHUNK)
            out[..., block, :] = sdpa(
                query[..., block, :], key, value,
                attn_mask=attn_mask[..., block, :] if sliceable else attn_mask,
                dropout_p=dropout_p, is_causal=False, scale=scale,
                enable_gqa=enable_gqa, **kwargs,
            )
        return out

    return wrapper


_sdpa_chunked = False


def _chunk_attention_globally() -> None:
    """Install the chunked wrapper over torch's SDPA, once per process.

    Patching torch itself rather than the pipeline is deliberate: Z-Image routes
    attention through diffusers' backend dispatcher, and its transformer has no
    set_attention_slice, so pipe.enable_attention_slicing() is a silent no-op here.
    """
    global _sdpa_chunked
    if _sdpa_chunked:
        return
    import torch

    torch.nn.functional.scaled_dot_product_attention = _chunked_sdpa(
        torch.nn.functional.scaled_dot_product_attention
    )
    _sdpa_chunked = True


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
        # The VAE was never the binding limit above 1024 - attention is. Same idea,
        # different tensor: block the score matrix so it never has to fit whole.
        _chunk_attention_globally()
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


DEFAULT_POSTER_SIZE = (960, 1344)  # the only rung measured working end to end


def poster_size() -> tuple[int, int]:
    """Which ladder rung posters generate at, from the `poster_size` setting.

    Anything not literally on POSTER_LADDER falls back to the measured default:
    every rung is exact 5:7 and 16-aligned, and a hand-edited setting must not be
    able to ask the GPU for a shape we never tested. 19 minutes is a long time to
    wait to find out a size doesn't decode.
    """
    import db

    raw = db.get_setting("poster_size") or ""
    try:
        width, height = (int(n) for n in raw.lower().split("x"))
    except ValueError:
        return DEFAULT_POSTER_SIZE
    return (width, height) if (width, height) in POSTER_LADDER else DEFAULT_POSTER_SIZE


# What a product IS, in one place. Same idea as the MODELS registry above: each
# seam looks its product up rather than branching on a string, so a third product
# is one dict entry instead of a hunt through six files.
#
# `size` is a callable so both products resolve the same way at call time - which
# is also what keeps Speed Production wired to tees only. Both entries call
# through a lambda rather than naming the function directly: a direct reference
# would bind the function object at import time, so monkeypatching
# pipeline.poster_size in a test would silently not take.
PRODUCTS = {
    "tee": {
        "label": "T-shirt",
        "template": PROMPT_TEMPLATE,
        "refine_key": "refine_prompt",
        "size": lambda: (current_size(), current_size()),
        "aspect": "1",
        "decode_share": 1 / (ZIMAGE_STEPS + 1),
        "eta_minutes": 6,
        "blueprint_key": "printify_blueprint_id",
        "blueprint_default": "6",   # Unisex Heavy Cotton Tee (Gildan 5000)
        "price_cents": 2499,
        "title_suffix": "T-Shirt",
    },
    "poster": {
        "label": "Poster (50x70cm)",
        "template": POSTER_TEMPLATE,
        "refine_key": "refine_prompt_poster",
        "size": lambda: poster_size(),
        "aspect": "5 / 7",
        # measured: 960x1344 spends ~19 of 19.5 min in the VAE decode, because
        # MIOpen has no CK grouped-conv library for gfx1031
        "decode_share": 0.95,
        "eta_minutes": 20,
        "blueprint_key": "printify_poster_blueprint_id",
        "blueprint_default": "",    # unknown: the catalogue endpoint 401s on this token
        "price_cents": 3499,        # placeholder until the blueprint is chosen
        "title_suffix": "Poster",
    },
}
DEFAULT_PRODUCT = "tee"


def product_data(name: str | None) -> dict:
    """Registry entry for a product. An unrecognised name falls back to the
    default rather than raising - same habit as current_model(), and for the same
    reason: a bad database value should degrade, not kill the worker thread."""
    return PRODUCTS.get(name or "", PRODUCTS[DEFAULT_PRODUCT])


def product_size(name: str | None) -> tuple[int, int]:
    """The (width, height) this product generates at, resolved now."""
    return product_data(name)["size"]()


def step_progress(step_index: int, steps: int, decode_share: float | None = None) -> int:
    """Percent to show after finishing step `step_index` (0-based) of `steps`.

    `decode_share` is the fraction of the bar reserved for the VAE decode that
    follows the loop. Omitted, it is 1 / (steps + 1), which makes this exactly the
    round((step_index + 1) / (steps + 1) * 100) this replaced - for every `steps`,
    not just the model's 9.

    A poster is the reason this is a parameter. Its decode is ~19 of 19.5 minutes
    (MIOpen has no CK grouped-conv library for gfx1031), so a loop that claimed
    90% of the bar in five seconds would read as a hung job for the next nineteen.
    """
    if decode_share is None:
        decode_share = 1 / (steps + 1)
    return round((step_index + 1) / steps * (1 - decode_share) * 100)


_pipe = None
_pipe_name = None


def generate_image_local(prompt: str, on_step=None, size: tuple[int, int] | None = None,
                         decode_share: float | None = None) -> bytes:
    """Generate one PNG on the local GPU (needs requirements-local.txt).
    Uses whichever model `image_model` names; on_step(pct) gets an int 0-100.
    `size` is an explicit (width, height) - posters need a non-square one and must
    not be shrunk by Speed Production. Omit it for the square t-shirt default.
    `decode_share` is how much of the progress bar to leave for the VAE decode;
    omit it for the tee default."""
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
            on_step(step_progress(step_index, steps, decode_share))
        return kwargs

    # current_size() is read per image, so the Speed toggle lands on the next one
    width, height = size or (current_size(), current_size())
    img = _pipe(
        prompt, num_inference_steps=steps, guidance_scale=0.0,
        width=width, height=height, callback_on_step_end=_cb,
    ).images[0]
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
