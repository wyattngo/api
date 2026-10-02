# syntax=docker/dockerfile:1.7
# Base: Runpod PyTorch (torch 2.8.0 + CUDA 12.8.1 + cuDNN đã cài sẵn, Python 3.11).
# Không cài lại torch ở đây — chỉ thêm diffusers (source local) + dependencies của app.
FROM runpod/pytorch:2.8.0-py3.11-cuda12.8.1-cudnn-devel-ubuntu22.04

ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Base image đặt CMD ["/start.sh"] (Jupyter/SSH) — bỏ đi, dùng handler của mình.
ENTRYPOINT []

# ============================================================
# DIFFUSERS — LOCAL SOURCE (bind mount: không để lại layer chứa source)
# ============================================================
RUN --mount=type=bind,source=diffusers,target=/tmp/diffusers,rw \
    python3 -m pip install /tmp/diffusers

# ============================================================
# PYTHON DEPENDENCIES
# ============================================================
COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install -r /tmp/requirements.txt \
    && python3 -c "import torch; assert torch.__version__.startswith('2.8.0'), torch.__version__; print('torch', torch.__version__, 'cuda', torch.version.cuda)"

# ============================================================
# MODELS BAKED INTO THE IMAGE (cold start no longer downloads them)
# Z-Image-Turbo (Apache-2.0) and the rembg ONNX model. Qwen-Image-2.1 is NOT baked in
# (Qwen Research License): use Runpod "Cached Models" for it, see cached_models_dir() in inference.py.
# The endpoint must not override HF_HOME / U2NET_HOME.
# ============================================================
ENV HF_HOME=/opt/hf
ENV U2NET_HOME=/opt/u2net
RUN python3 - <<'PY'
from huggingface_hub import snapshot_download
snapshot_download("Tongyi-MAI/Z-Image-Turbo")
from rembg import new_session
new_session("isnet-general-use")
print("baked: Z-Image-Turbo + isnet-general-use")
PY

# ============================================================
# APPLICATION
# ============================================================
WORKDIR /app
COPY inference.py /app/inference.py
COPY runpod_handler.py /app/runpod_handler.py

# ============================================================
# QWEN WORKER CONFIG
# ============================================================
ENV APP_DEVICE=cuda
ENV APP_DTYPE=bf16

# RTX 4090 24GB
ENV APP_CPU_OFFLOAD=1

# Turbo / Prompt Enhancer
ENV APP_TURBO=1
ENV APP_PROMPT_ENHANCER=1

# ============================================================
# RUNPOD SERVERLESS
# ============================================================
CMD ["python3", "-u", "/app/runpod_handler.py"]
