"""Local Real-ESRGAN 4x upscale: 1024px design -> ~4096px print file."""
import os
import threading

import db

_model = None
_lock = threading.Lock()  # ponytail: one upscale at a time on an 8GB machine

WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weights", "RealESRGAN_x4.pth")


def _get_model():
    global _model
    if _model is None:
        import torch
        from py_real_esrgan.model import RealESRGAN

        device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
        _model = RealESRGAN(device, scale=4)
        _model.load_weights(WEIGHTS, download=True)
    return _model


def upscale(design_id: int, src_path: str) -> None:
    """Fire-and-forget: upscale in a background thread, record print_file when done."""

    def job():
        with _lock:
            try:
                from PIL import Image

                img = Image.open(src_path).convert("RGB")
                result = _get_model().predict(img)
                out_path = src_path[: -len(".png")] + "_print.png"
                result.save(out_path)
                rel = os.path.join("designs", os.path.basename(out_path))
                with db.connect() as con:
                    con.execute(
                        "UPDATE designs SET print_file = ? WHERE id = ?", (rel, design_id)
                    )
            except Exception as e:
                # design stays approved; publish falls back to the 1024px original
                with db.connect() as con:
                    con.execute(
                        "UPDATE designs SET error = ? WHERE id = ?",
                        (("upscale failed: %s" % e)[:500], design_id),
                    )

    threading.Thread(target=job, daemon=True).start()
