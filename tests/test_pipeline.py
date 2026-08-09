import pytest

import db
import pipeline
from pipeline import parse_input, build_prompt, step_progress

torch = pytest.importorskip("torch")


def test_parse_explodes_one_row_per_filter_term():
    text = "funny fishing shirt | vintage, distressed, black shirt\nplant mom | retro 70s, floral\n"
    assert parse_input(text) == [
        ("funny fishing shirt", "vintage"),
        ("funny fishing shirt", "distressed"),
        ("funny fishing shirt", "black shirt"),
        ("plant mom", "retro 70s"),
        ("plant mom", "floral"),
    ]


def test_parse_bare_phrase_and_blank_lines():
    assert parse_input("\ndog dad\n\n") == [("dog dad", "")]


def test_parse_strips_messy_whitespace():
    assert parse_input("  cat mom  |  cute ,  pastel  ") == [("cat mom", "cute"), ("cat mom", "pastel")]


def test_parse_skips_empty_phrase():
    assert parse_input("| vintage") == []


def test_prompt_includes_phrase_and_filters():
    p = build_prompt("dog dad", "minimalist, line art")
    assert "dog dad" in p and "minimalist, line art" in p


def test_prompt_without_filters_has_no_style_clause():
    assert "Style:" not in build_prompt("dog dad", "")


def test_step_progress_maps_steps_to_reserved_percent():
    assert [step_progress(i, 4) for i in range(4)] == [20, 40, 60, 80]


def test_step_progress_monotonic_and_below_100():
    pct = [step_progress(i, 4) for i in range(4)]
    assert pct == sorted(pct)
    assert max(pct) < 100


# 50x70cm poster ladder. dpi is what decides whether a size is sellable, so the
# ladder is ordered by it and the probe walks it top-down.
def test_poster_dpi_top_rung_is_effectively_300():
    assert pipeline.poster_dpi(1440, 2016) == 292


def test_poster_dpi_uses_the_worse_axis():
    # a wide-but-short image must not get to claim the width's dpi
    assert pipeline.poster_dpi(1440, 1008) == 146


def test_poster_dpi_scales_with_the_upscale_factor():
    assert pipeline.poster_dpi(1440, 2016, upscale=1) == 73


def test_poster_ladder_descends_in_quality():
    dpis = [pipeline.poster_dpi(w, h) for w, h in pipeline.POSTER_LADDER]
    assert dpis == sorted(dpis, reverse=True)
    assert len(set(dpis)) == len(dpis)      # no rung is a pointless repeat of another


def test_poster_ladder_holds_an_exact_5_7_aspect():
    assert all(w * 7 == h * 5 for w, h in pipeline.POSTER_LADDER)


def test_poster_ladder_sides_are_16_aligned():
    # latent is /8 with a /2 patch step; 16 is the alignment that never needs padding
    assert all(w % 16 == 0 and h % 16 == 0 for w, h in pipeline.POSTER_LADDER)


def test_poster_ladder_spans_print_quality_down_to_the_large_format_floor():
    dpis = [pipeline.poster_dpi(w, h) for w, h in pipeline.POSTER_LADDER]
    assert dpis[0] >= 290       # top rung is worth attempting
    assert dpis[-1] <= 150      # bottom rung proves non-square works even if unsellable


class _FakeImage:
    def save(self, buf, fmt):
        buf.write(b"not-really-a-png")


class _FakePipe:
    """Stands in for the loaded ZImagePipeline so the size logic is testable
    without 15GB of weights or a GPU."""

    def __init__(self):
        self.kwargs = None

    def __call__(self, prompt, **kwargs):
        self.kwargs = kwargs
        import types
        return types.SimpleNamespace(images=[_FakeImage()])


def _fake_loaded_pipe(monkeypatch):
    """Pretend the model for the current name is already loaded, so
    generate_image_local skips the builder entirely."""
    fake = _FakePipe()
    monkeypatch.setattr(pipeline, "current_model", lambda: pipeline.DEFAULT_MODEL)
    monkeypatch.setattr(pipeline, "_pipe", fake)
    monkeypatch.setattr(pipeline, "_pipe_name", pipeline.DEFAULT_MODEL)
    return fake


def test_generate_uses_an_explicit_size_verbatim(monkeypatch):
    fake = _fake_loaded_pipe(monkeypatch)
    pipeline.generate_image_local("a poster", size=(1040, 1456))
    assert (fake.kwargs["width"], fake.kwargs["height"]) == (1040, 1456)


def test_generate_without_a_size_stays_square_at_current_size(monkeypatch):
    fake = _fake_loaded_pipe(monkeypatch)
    monkeypatch.setattr(pipeline, "current_size", lambda: 512)
    pipeline.generate_image_local("a t-shirt graphic")
    assert (fake.kwargs["width"], fake.kwargs["height"]) == (512, 512)


def test_generate_ignores_speed_production_when_a_size_is_given(monkeypatch):
    # Speed Production halves the edge for throughput; a poster asked for at an
    # exact size must not get silently shrunk to an unprintable one.
    fake = _fake_loaded_pipe(monkeypatch)
    monkeypatch.setattr(pipeline, "current_size", lambda: 512)
    pipeline.generate_image_local("a poster", size=(1440, 2016))
    assert (fake.kwargs["width"], fake.kwargs["height"]) == (1440, 2016)


def test_generate_returns_the_saved_image_bytes(monkeypatch):
    _fake_loaded_pipe(monkeypatch)
    assert pipeline.generate_image_local("x", size=(720, 1008)) == b"not-really-a-png"


# Chunked attention. gfx1031 has neither the flash nor the memory-efficient SDPA
# kernel, so torch falls back to MATH and materialises the full NxN score matrix -
# measured 26.6GB at the top poster rung against a 10GB card. Slicing the queries is
# exact (each row's softmax is independent), but only if masks and causality are
# handled properly, which is what these pin.
def _sdpa_pair(monkeypatch, above=8, chunk=4):
    """The wrapper plus the real SDPA, with the threshold dropped so tiny test
    tensors take the chunking path."""
    import torch.nn.functional as F
    monkeypatch.setattr(pipeline, "ATTENTION_CHUNK_ABOVE", above)
    monkeypatch.setattr(pipeline, "ATTENTION_CHUNK", chunk)
    return pipeline._chunked_sdpa(F.scaled_dot_product_attention), F.scaled_dot_product_attention


def test_chunked_attention_is_exact_not_an_approximation(monkeypatch):
    wrapped, real = _sdpa_pair(monkeypatch)
    torch.manual_seed(0)
    q, k, v = (torch.randn(1, 2, 20, 8) for _ in range(3))
    assert torch.allclose(wrapped(q, k, v), real(q, k, v), atol=1e-6)


def test_chunked_attention_slices_a_per_query_mask_with_the_queries(monkeypatch):
    # a mask with a real query dimension must be cut to match its block, or every
    # block after the first silently attends through the wrong rows
    wrapped, real = _sdpa_pair(monkeypatch)
    torch.manual_seed(1)
    q, k, v = (torch.randn(1, 2, 20, 8) for _ in range(3))
    mask = torch.rand(1, 2, 20, 20) > 0.3
    mask[..., 0] = True                      # no row may mask out everything
    assert torch.allclose(wrapped(q, k, v, attn_mask=mask), real(q, k, v, attn_mask=mask), atol=1e-6)


def test_chunked_attention_leaves_a_broadcast_mask_alone(monkeypatch):
    # [B, 1, 1, S_k] broadcasts over queries, so slicing it would be wrong
    wrapped, real = _sdpa_pair(monkeypatch)
    torch.manual_seed(2)
    q, k, v = (torch.randn(1, 2, 20, 8) for _ in range(3))
    mask = torch.zeros(1, 1, 1, 20)
    assert torch.allclose(wrapped(q, k, v, attn_mask=mask), real(q, k, v, attn_mask=mask), atol=1e-6)


def test_chunked_attention_refuses_to_chunk_causal_attention(monkeypatch):
    # is_causal is positional-dependent; a block would mask against its own offset
    # rather than the sequence's, so this must fall through untouched
    wrapped, real = _sdpa_pair(monkeypatch)
    torch.manual_seed(3)
    q, k, v = (torch.randn(1, 2, 20, 8) for _ in range(3))
    assert torch.allclose(wrapped(q, k, v, is_causal=True), real(q, k, v, is_causal=True), atol=1e-6)


def test_short_sequences_take_the_untouched_path(monkeypatch):
    """t-shirts must keep running on exactly the code path they always have."""
    calls = []

    def spy(query, key, value, **kw):
        calls.append(query.shape[-2])
        return torch.zeros_like(query)

    monkeypatch.setattr(pipeline, "ATTENTION_CHUNK_ABOVE", 4096)
    monkeypatch.setattr(pipeline, "ATTENTION_CHUNK", 1024)
    wrapped = pipeline._chunked_sdpa(spy)
    wrapped(torch.zeros(1, 2, 4096, 8), torch.zeros(1, 2, 4096, 8), torch.zeros(1, 2, 4096, 8))
    assert calls == [4096]      # 1024x1024's token count: one call, whole tensor


def test_long_sequences_are_split_into_blocks(monkeypatch):
    calls = []

    def spy(query, key, value, **kw):
        calls.append(query.shape[-2])
        return torch.zeros_like(query)

    monkeypatch.setattr(pipeline, "ATTENTION_CHUNK_ABOVE", 4096)
    monkeypatch.setattr(pipeline, "ATTENTION_CHUNK", 1024)
    wrapped = pipeline._chunked_sdpa(spy)
    wrapped(torch.zeros(1, 2, 5040, 8), torch.zeros(1, 2, 5040, 8), torch.zeros(1, 2, 5040, 8))
    assert calls == [1024, 1024, 1024, 1024, 944]       # 960x1344's token count
    assert max(calls) <= 1024


# _build_zimage can't run without a GPU and 15GB of weights, but the offload/tiling
# calls it makes are pure API surface — and getting one wrong is silent until an
# image is actually generated. It shipped calling pipe.enable_vae_tiling(), which
# ZImagePipeline does not have. These pin the two methods the builder depends on.
diffusers = pytest.importorskip("diffusers")


def test_vae_exposes_the_tiling_method_the_builder_calls():
    # gfx103x MIOpen has no kernel for a full 1024 decode; without tiling the GPU faults
    assert hasattr(diffusers.AutoencoderKL, "enable_tiling")


def test_zimage_pipeline_exposes_model_cpu_offload():
    assert hasattr(diffusers.ZImagePipeline, "enable_model_cpu_offload")


# Speed Production: generate at 512 instead of 1024. The VAE decode is ~94% of
# wall-clock on a card with no optimised conv kernel, and it scales with pixels,
# so halving each side is the one lever that actually moves the number.
def test_size_defaults_to_full_when_unset(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "s.db"))
    db.init()
    assert pipeline.current_size() == pipeline.FULL_SIZE == 1024


def test_speed_production_on_gives_512(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "s.db"))
    db.init()
    db.set_setting("speed_production", "on")
    assert pipeline.current_size() == pipeline.FAST_SIZE == 512


def test_speed_production_off_gives_1024(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "s.db"))
    db.init()
    db.set_setting("speed_production", "off")   # explicit off must round-trip, not just unset
    assert pipeline.current_size() == 1024


def test_unrecognised_speed_value_falls_back_to_full(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "s.db"))
    db.init()
    db.set_setting("speed_production", "yes-please")
    assert pipeline.current_size() == 1024      # never hand the pipeline a junk size


def _mock_zimage_build(monkeypatch, *, hip):
    """Run _build_zimage with the 15GB of loading stubbed out.

    The pipe is specced against the real ZImagePipeline, so calling a method that
    class doesn't have raises AttributeError instead of silently passing - which is
    how `pipe.enable_vae_tiling()` shipped broken.
    """
    import transformers
    from unittest.mock import MagicMock

    spec = [m for m in dir(diffusers.ZImagePipeline) if not m.startswith("__")] + ["vae"]
    pipe = MagicMock(spec=spec)
    pipe.vae = MagicMock(spec=[m for m in dir(diffusers.AutoencoderKL) if not m.startswith("__")])

    monkeypatch.setattr(diffusers.ZImageTransformer2DModel, "from_single_file",
                        classmethod(lambda cls, *a, **k: MagicMock()))
    monkeypatch.setattr(transformers.Qwen3Model, "from_pretrained",
                        classmethod(lambda cls, *a, **k: MagicMock()))
    monkeypatch.setattr(diffusers.ZImagePipeline, "from_pretrained",
                        classmethod(lambda cls, *a, **k: pipe))
    monkeypatch.setattr(torch.version, "hip", "7.13" if hip else None)
    # stubbed, not just observed: the real one patches torch.nn.functional for the
    # whole process, which has no business leaking out of a unit test
    monkeypatch.setattr(pipeline, "_chunk_attention_globally", MagicMock())
    return pipeline._build_zimage(), pipe


def test_build_zimage_only_calls_methods_the_pipeline_really_has(monkeypatch):
    returned, pipe = _mock_zimage_build(monkeypatch, hip=True)
    assert returned is pipe
    pipe.enable_model_cpu_offload.assert_called_once()


def test_build_zimage_tiles_the_vae_on_rocm(monkeypatch):
    # gfx103x has no MIOpen kernel for a full 1024 decode; untiled it faults the GPU
    _, pipe = _mock_zimage_build(monkeypatch, hip=True)
    pipe.vae.enable_tiling.assert_called_once()


def test_build_zimage_skips_vae_tiling_off_rocm(monkeypatch):
    _, pipe = _mock_zimage_build(monkeypatch, hip=False)
    pipe.vae.enable_tiling.assert_not_called()


def test_build_zimage_chunks_attention_on_rocm(monkeypatch):
    # without this the top poster rung asks for 26.6GB of score matrix on a 10GB card
    _mock_zimage_build(monkeypatch, hip=True)
    pipeline._chunk_attention_globally.assert_called_once()


def test_build_zimage_leaves_attention_alone_off_rocm(monkeypatch):
    # CUDA has flash attention, so chunking would only cost kernel launches
    _mock_zimage_build(monkeypatch, hip=False)
    pipeline._chunk_attention_globally.assert_not_called()


def test_poster_template_takes_the_same_placeholders_as_the_tee_one():
    filled = pipeline.POSTER_TEMPLATE.format(phrase="a lighthouse", style="Style: art deco. ")
    assert "a lighthouse" in filled and "art deco" in filled


def test_poster_template_says_what_it_wants_not_what_it_doesnt():
    # Z-Image-Turbo runs at guidance_scale=0.0, so with no classifier-free guidance
    # negative phrasing barely binds - the probe asked for "no frame, no border" and
    # got a prominent frame. This template must not repeat that mistake.
    assert "no " not in pipeline.POSTER_TEMPLATE.lower()


def test_poster_template_asks_for_one_subject_filling_a_tall_canvas():
    t = pipeline.POSTER_TEMPLATE.lower()
    assert "one clear focal subject" in t
    assert "edge to edge" in t


def test_poster_size_defaults_to_the_measured_rung(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    assert pipeline.poster_size() == (960, 1344)


def test_poster_size_reads_a_saved_rung(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    db.set_setting("poster_size", "1120x1568")
    assert pipeline.poster_size() == (1120, 1568)


def test_poster_size_rejects_a_size_that_is_not_on_the_ladder(tmp_path, monkeypatch):
    # a hand-edited setting must not be able to ask the GPU for a size we never
    # tested - every rung is 16-aligned and exact 5:7, and an arbitrary one is not
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    db.set_setting("poster_size", "2000x3000")
    assert pipeline.poster_size() == pipeline.DEFAULT_POSTER_SIZE


def test_poster_size_survives_junk(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    for junk in ("", "big", "960", "960x", "axb", "960x1344x99"):
        db.set_setting("poster_size", junk)
        assert pipeline.poster_size() == pipeline.DEFAULT_POSTER_SIZE


def test_default_poster_size_is_actually_on_the_ladder():
    assert pipeline.DEFAULT_POSTER_SIZE in pipeline.POSTER_LADDER


PRODUCT_KEYS = {"label", "template", "refine_key", "size", "aspect", "decode_share",
                "eta_minutes", "blueprint_key", "blueprint_default", "price_cents",
                "title_suffix"}


def test_every_product_carries_every_key():
    # a missing key is a KeyError 19 minutes into a generation run, so check the
    # shape of the registry rather than trusting each call site to be careful
    for name, data in pipeline.PRODUCTS.items():
        assert set(data) == PRODUCT_KEYS, name


def test_default_product_is_in_the_registry():
    assert pipeline.DEFAULT_PRODUCT in pipeline.PRODUCTS


def test_product_data_falls_back_like_current_model_does():
    assert pipeline.product_data("poster")["label"] == "Poster (50x70cm)"
    assert pipeline.product_data("hoodie") is pipeline.PRODUCTS["tee"]
    assert pipeline.product_data(None) is pipeline.PRODUCTS["tee"]
    assert pipeline.product_data("") is pipeline.PRODUCTS["tee"]


def test_tee_size_is_square_and_still_obeys_speed_production(monkeypatch):
    monkeypatch.setattr(pipeline, "current_size", lambda: 512)
    assert pipeline.product_size("tee") == (512, 512)
    monkeypatch.setattr(pipeline, "current_size", lambda: 1024)
    assert pipeline.product_size("tee") == (1024, 1024)


def test_poster_size_ignores_speed_production(monkeypatch):
    # a 512-tall poster is unprintable; Speed Production is a t-shirt sifting tool
    monkeypatch.setattr(pipeline, "current_size", lambda: 512)
    monkeypatch.setattr(pipeline, "poster_size", lambda: (960, 1344))
    assert pipeline.product_size("poster") == (960, 1344)


def test_unknown_product_generates_as_a_tee(monkeypatch):
    monkeypatch.setattr(pipeline, "current_size", lambda: 1024)
    assert pipeline.product_size("hoodie") == (1024, 1024)


def test_tee_decode_share_matches_the_old_reserved_fraction():
    assert pipeline.PRODUCTS["tee"]["decode_share"] == 1 / (pipeline.ZIMAGE_STEPS + 1)


def test_poster_aspect_is_the_5_7_the_ladder_generates():
    assert pipeline.PRODUCTS["poster"]["aspect"] == "5 / 7"
    assert pipeline.PRODUCTS["tee"]["aspect"] == "1"


def test_tee_blueprint_default_is_the_gildan_tee_it_always_was():
    assert pipeline.PRODUCTS["tee"]["blueprint_default"] == "6"


def test_build_prompt_defaults_to_the_tee_template():
    # every existing caller passes two arguments and must keep getting a tee
    assert build_prompt("dog dad", "") == build_prompt("dog dad", "", "tee")
    assert "t-shirt" in build_prompt("dog dad", "").lower()


def test_build_prompt_uses_the_poster_template_for_posters():
    p = build_prompt("a lighthouse at sunset", "art deco", "poster")
    assert "poster print" in p.lower()
    assert "t-shirt" not in p.lower()
    assert "a lighthouse at sunset" in p and "art deco" in p


def test_build_prompt_style_clause_works_for_both_products():
    for product in pipeline.PRODUCTS:
        assert "Style:" not in build_prompt("dog dad", "", product)
        assert "Style: vintage." in build_prompt("dog dad", "vintage", product)


def test_build_prompt_falls_back_to_tee_on_an_unknown_product():
    assert build_prompt("dog dad", "", "hoodie") == build_prompt("dog dad", "")


def test_step_progress_is_unchanged_for_tees():
    # the old formula was round((i + 1) / (steps + 1) * 100); the new one must be
    # arithmetically identical at the tee's decode share, not merely close
    steps = pipeline.ZIMAGE_STEPS
    for i in range(steps):
        assert step_progress(i, steps) == round((i + 1) / (steps + 1) * 100)


def test_step_progress_still_reserves_the_top_of_the_bar():
    assert step_progress(pipeline.ZIMAGE_STEPS - 1, pipeline.ZIMAGE_STEPS) == 90


def test_step_progress_gives_a_poster_loop_only_its_real_share():
    # a poster spends ~19 of 19.5 minutes decoding: the loop must not claim 90%
    # of the bar in five seconds and then sit there
    steps = pipeline.ZIMAGE_STEPS
    assert step_progress(steps - 1, steps, decode_share=0.95) == 5
    assert step_progress(0, steps, decode_share=0.95) == 1


def test_generate_passes_the_decode_share_through_to_the_callback(monkeypatch):
    fake = _fake_loaded_pipe(monkeypatch)
    seen = []
    pipeline.generate_image_local("a poster", on_step=seen.append,
                                  size=(960, 1344), decode_share=0.95)
    fake.kwargs["callback_on_step_end"](None, pipeline.ZIMAGE_STEPS - 1, None, {})
    assert seen == [5]


def test_generate_without_a_decode_share_reports_like_a_tee(monkeypatch):
    fake = _fake_loaded_pipe(monkeypatch)
    seen = []
    pipeline.generate_image_local("a tee", on_step=seen.append)
    fake.kwargs["callback_on_step_end"](None, pipeline.ZIMAGE_STEPS - 1, None, {})
    assert seen == [90]
