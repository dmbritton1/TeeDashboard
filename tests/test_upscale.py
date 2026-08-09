import pytest

import db
import upscale

torch = pytest.importorskip("torch")


def test_device_prefers_the_gpu(monkeypatch):
    # ROCm reports as cuda in torch, so this one branch covers the RX 6700
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert upscale._device().type == "cuda"


def test_device_falls_back_to_mps_then_cpu(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    assert upscale._device().type == "mps"
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: False)
    assert upscale._device().type == "cpu"


def test_models_are_cached_per_device(monkeypatch):
    # two devices must not share one loaded model, and asking twice for the same
    # device must not reload 60MB of weights
    made = []

    def fake_build(device):
        made.append(device.type)
        return object()

    monkeypatch.setattr(upscale, "_build_model", fake_build)
    monkeypatch.setattr(upscale, "_models", {})
    a = upscale._get_model(torch.device("cpu"))
    b = upscale._get_model(torch.device("cpu"))
    assert a is b and made == ["cpu"]
    c = upscale._get_model(torch.device("cuda"))
    assert c is not a and made == ["cpu", "cuda"]
    # and the second device's build must not have evicted the first
    assert upscale._get_model(torch.device("cpu")) is a


def test_job_device_steps_aside_while_a_design_is_generating(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    db.init()
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert upscale._job_device().type == "cuda"   # idle queue: GPU
    with db.connect() as con:
        con.execute("INSERT INTO designs (phrase, status) VALUES ('x', 'generating')")
    assert upscale._job_device().type == "cpu"    # generation in flight: yield
