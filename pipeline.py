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


def generate_image(prompt: str, api_key: str) -> bytes:
    """Generate one PNG via Gemini. Swappable: replace this to use another model."""
    from google import genai

    client = genai.Client(api_key=api_key)
    resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    for part in resp.candidates[0].content.parts:
        if part.inline_data and part.inline_data.data:
            return part.inline_data.data
    raise RuntimeError("Gemini returned no image (text: %s)" % (getattr(resp, "text", "") or "empty"))


FLUX_MODEL = "black-forest-labs/FLUX.1-schnell"
# 4-bit transformer: 6.9GB instead of the 24GB bf16 one, so it fits in a 10GB card.
FLUX_GGUF = (
    "https://huggingface.co/unsloth/FLUX.1-schnell-GGUF/blob/main/flux1-schnell-Q4_K_M.gguf"
)

_has_local = None
_flux = None


def has_local() -> bool:
    """True when an NVIDIA GPU is available: generate locally instead of via Gemini."""
    global _has_local
    if _has_local is None:
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


def generate_image_local(prompt: str) -> bytes:
    """Generate one PNG with FLUX.1-schnell on the local GPU (needs requirements-local.txt)."""
    global _flux
    import io

    if _flux is None:
        _flux = _build_flux()
    img = _flux(prompt, num_inference_steps=4, guidance_scale=0.0, width=1024, height=1024).images[0]
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()
