FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# ============================================================
# SYSTEM
# ============================================================

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    git \
    wget \
    curl \
    ca-certificates \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# PYTHON VENV
# ============================================================

RUN python3 -m venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade \
    pip \
    setuptools \
    wheel

# ============================================================
# PYTORCH CUDA 12.8
# ============================================================

RUN pip install \
    torch==2.8.0 \
    torchvision==0.23.0 \
    torchaudio==2.8.0 \
    --index-url https://download.pytorch.org/whl/cu128

# ============================================================
# DIFFUSERS — LOCAL SOURCE
# ============================================================

COPY diffusers /tmp/diffusers

RUN pip install /tmp/diffusers

# ============================================================
# PYTHON DEPENDENCIES
# ============================================================

COPY requirements.txt /tmp/requirements.txt

RUN pip install -r /tmp/requirements.txt

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

CMD ["python3", "/app/runpod_handler.py"]