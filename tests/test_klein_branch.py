"""FLUX.2 klein trial branch: text/fast picks klein only when APP_FAST_TEXT_MODEL=flux2-klein."""
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_DEVICE", "cpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inference as I  # noqa: E402
from PIL import Image  # noqa: E402


class FakePipe:
    def __init__(self):
        self.kwargs = None

    def __call__(self, **kwargs):
        self.kwargs = kwargs

        class Out:
            images = [Image.new("RGB", (8, 8))]
        return Out()


def run(monkeypatch, model):
    fake = FakePipe()
    monkeypatch.setattr(I, "FAST_TEXT_MODEL", model)
    monkeypatch.setattr(I, "get_klein_pipeline", lambda: fake)
    monkeypatch.setattr(I, "get_zimage_pipeline", lambda: fake)
    monkeypatch.setattr(I, "free_memory", lambda deep=False: None)
    monkeypatch.setattr(I, "supports_step_callback", lambda pipe: False)
    result = I._generate_blocking("text", "a cat", [], 64, 64, 40, 1, False, "", quality="fast")
    assert result["success"], result
    (I.OUTPUT_DIR / result["filename"]).unlink()
    return fake.kwargs


def test_klein_uses_four_steps(monkeypatch):
    kw = run(monkeypatch, "flux2-klein")
    assert kw["num_inference_steps"] == 4 and kw["guidance_scale"] == 1.0


def test_default_stays_zimage(monkeypatch):
    kw = run(monkeypatch, "zimage")
    assert kw["num_inference_steps"] == 9 and kw["guidance_scale"] == 0.0
