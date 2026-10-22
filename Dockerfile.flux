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
# MODEL BAKED INTO THE IMAGE: FLUX.2 klein 4B (Apache-2.0), TRIAL IMAGE ONLY
# (docs/design-flux-klein-trial.md). Neither Z-Image nor Qwen is baked in.
# If the repo is gated, build with:  --secret id=hf_token,env=HF_TOKEN  (the token is not kept in a layer).
# The endpoint must not override HF_HOME.
# ============================================================
ENV HF_HOME=/opt/hf
RUN --mount=type=secret,id=hf_token,required=false \
    python3 - <<'PY'
import os
from huggingface_hub import snapshot_download
token = None
if os.path.exists("/run/secrets/hf_token"):
    token = open("/run/secrets/hf_token").read().strip() or None
snapshot_download("black-forest-labs/FLUX.2-klein-4B", token=token)
print("baked: FLUX.2-klein-4B")
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


# Turbo / Prompt Enhancer
ENV APP_TURBO=0
ENV APP_PROMPT_ENHANCER=0

# Trial: text/fast runs FLUX.2 klein; 24 GB card, keep the model on the GPU
ENV APP_FAST_TEXT_MODEL=flux2-klein
ENV APP_FLUX_FEATURES=all
ENV APP_FLUX_ONLY=1
ENV APP_CPU_OFFLOAD=0
ENV RUNPOD_PRELOAD=flux2klein

# ============================================================
# RUNPOD SERVERLESS
# ============================================================
CMD ["python3", "-u", "/app/runpod_handler.py"]
