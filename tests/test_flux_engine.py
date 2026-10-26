"""Engine selection for batch / rerender and the model reported back to the client."""
import os
import sys
from pathlib import Path

os.environ.setdefault("APP_DEVICE", "cpu")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inference as I  # noqa: E402
import pytest  # noqa: E402
import runpod_handler as H  # noqa: E402
from PIL import Image  # noqa: E402


def png_data_uri(tmp_path):
    import base64
    import io
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 30, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def stub_run_8_cases(monkeypatch):
    seen = {}

    def fake(job_id, images, aspect_ratio, resolution, steps, seed, description, remove_bg, mode,
             prompt_enhance=True, aspect_auto=False, category="generic", use_flux=False):
        seen["use_flux"] = use_flux
        case_dir = I.BATCH_DIR / job_id
        case_dir.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8)).save(case_dir / "01_x.png")
        I.update_batch(job_id, status="completed", completed=1,
                       results=[{"case": 1, "status": "completed", "filename": "01_x.png"}])

    monkeypatch.setattr(I, "run_8_cases", fake)
    monkeypatch.setattr(I, "FLUX_FEATURES", set())
    return seen


def batch(quality=None, mode="draft"):
    inp = {"action": "batch", "images": [png_data_uri(None)], "mode": mode, "output": "base64"}
    if quality:
        inp["quality"] = quality
    return H.handler({"id": "t", "input": inp})


def test_batch_defaults_to_qwen(stub_run_8_cases):
    out = batch()
    assert out["params"]["engine"] == "qwen" and stub_run_8_cases["use_flux"] is False


def test_batch_quality_flux_runs_flux_even_for_final(stub_run_8_cases):
    out = batch("flux", mode="final")
    assert out["params"]["engine"] == "flux" and stub_run_8_cases["use_flux"] is True


def test_batch_feature_flag_moves_draft_only(stub_run_8_cases, monkeypatch):
    monkeypatch.setattr(I, "FLUX_FEATURES", {"batch"})
    assert batch(mode="draft")["params"]["engine"] == "flux"
    assert batch(mode="final")["params"]["engine"] == "qwen"


@pytest.fixture
def stub_rerender(monkeypatch):
    seen = {}

    def fake(job_id, numbers, steps, use_flux=False):
        seen["use_flux"] = use_flux
        case_dir = I.BATCH_DIR / job_id
        for n in numbers:
            Image.new("RGB", (8, 8)).save(case_dir / f"{n:02d}_x_final.png")
        I.update_batch(job_id, status="completed")

    monkeypatch.setattr(I, "run_rerender", fake)
    monkeypatch.setattr(I, "FLUX_FEATURES", set())
    return seen


def rerender(quality=None):
    inp = {"action": "rerender", "images": [png_data_uri(None)], "cases": [1], "output": "base64",
           "params": {"seed": 1, "aspect_ratio": "1:1"}}
    if quality:
        inp["quality"] = quality
    return H.handler({"id": "t", "input": inp})


def test_rerender_defaults_to_qwen(stub_rerender):
    out = rerender()
    assert out["engine"] == "qwen" and stub_rerender["use_flux"] is False


def test_rerender_quality_flux(stub_rerender):
    out = rerender("flux")
    assert out["engine"] == "flux" and stub_rerender["use_flux"] is True


def test_rerender_feature_flag(stub_rerender, monkeypatch):
    monkeypatch.setattr(I, "FLUX_FEATURES", {"rerender"})
    assert rerender()["engine"] == "flux"


def test_rerender_session_class_follows_use_flux(monkeypatch):
    chosen = []

    class Boom(Exception):
        pass

    def fake_session(cls):
        def make(draft, notify=None):
            chosen.append(cls)
            raise Boom
        return make

    I.new_job_id = getattr(I, "new_job_id", None)
    monkeypatch.setattr(I, "FLUX_FEATURES", set())
    monkeypatch.setattr(I, "load_references", lambda d: [Image.new("RGB", (8, 8))])
    monkeypatch.setattr(I, "QwenSession", fake_session("qwen"))
    monkeypatch.setattr(I, "KleinSession", fake_session("klein"))
    for flag, want in ((False, "qwen"), (True, "klein")):
        job_id = f"{'a' if flag else 'b'}00000000000"
        H.new_job(job_id, {"seed": 1, "aspect_ratio": "1:1", "resolution": 1024, "ref_count": 1},
                  results=[{"case": 1}])
        I.run_rerender(job_id, [1], 20, flag)
        H.forget_job(job_id)
        assert chosen[-1] == want
