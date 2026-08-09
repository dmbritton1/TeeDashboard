"""Local Real-ESRGAN 4x upscale: 1024px design -> ~4096px print file."""
import os
import threading

import db

_models = {}          # device type -> loaded model; only touched inside job()'s _lock, which is what makes this dict safe
_lock = threading.Lock()  # ponytail: one upscale at a time on an 8GB machine

WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "RealESRGAN_x4.pth")


def _device():
    """Best available device. ROCm reports as cuda in torch, so the first branch
    covers the RX 6700 - without it every upscale runs on CPU, which is tolerable
    at 1024px and painful at a poster's 960x1344."""
    import torch

    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _job_device():
    """CPU if a design was generating when this upscale started: the 10GB card
    can't hold Real-ESRGAN's patches next to a poster's denoise or decode, and
    generation has no OOM fallback - a fault there loses a 19-minute run. A
    point-in-time check, not a reservation, but it closes the dominant window
    (approving designs during a long generation), and the inverse race is
    bounded by the OOM fallback below. On any doubt, fail toward the safe
    device rather than failing the upscale."""
    import torch

    try:
        with db.connect() as con:
            busy = con.execute(
                "SELECT 1 FROM designs WHERE status = 'generating' LIMIT 1"
            ).fetchone()
    except Exception:
        return torch.device("cpu")
    return torch.device("cpu") if busy else _device()


def _build_model(device):
    from py_real_esrgan.model import RealESRGAN

    model = RealESRGAN(device, scale=4)
    model.load_weights(WEIGHTS, download=True)
    return model


def _get_model(device):
    """Cached per device: the OOM path below reloads on CPU, and that must not
    evict the GPU model for the next design."""
    if device.type not in _models:
        _models[device.type] = _build_model(device)
    return _models[device.type]


def upscale(design_id: int, src_path: str) -> None:
    """Fire-and-forget: upscale in a background thread, record print_file when done."""

    def job():
        with _lock:
            try:
                import torch
                from PIL import Image

                img = Image.open(src_path).convert("RGB")
                try:
                    result = _get_model(_job_device()).predict(img)
                except torch.OutOfMemoryError:
                    # a poster is 1.7x a tee's pixels and 4x output is 3840x5376;
                    # slow on CPU beats no print file at all
                    result = _get_model(torch.device("cpu")).predict(img)
                out_path = os.path.splitext(src_path)[0] + "_print.png"
                result.save(out_path)
                rel = os.path.join("designs", os.path.basename(out_path))
                with db.connect() as con:
                    con.execute(
                        "UPDATE designs SET print_file = ?, error = NULL WHERE id = ?", (rel, design_id)
                    )
            except Exception as e:
                # design stays approved; publish falls back to the original
                with db.connect() as con:
                    con.execute(
                        "UPDATE designs SET error = ? WHERE id = ?",
                        (("upscale failed: %s" % e)[:500], design_id),
                    )

    threading.Thread(target=job, daemon=True).start()
