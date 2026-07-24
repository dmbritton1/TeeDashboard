import pytest

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
