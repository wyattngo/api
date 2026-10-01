"""
inference.py — GPU worker: mọi thứ liên quan tới model, ảnh tham chiếu, prompt và hàng đợi GPU.

KHÔNG import FastAPI / Jinja2 / UploadFile. Có thể chạy độc lập (import rồi dùng), hoặc
được app.py (web layer) import. Giữ nguyên logic của app.py gốc:
  prepare_reference / prepare_reference_set / postprocess_transparent_output
  Prompt Enhancer (enhance_prompt, enhance_case_prompts, unload_prompt_enhancer)
  prepare_turbo / set_qwen_mode / QwenSession
  _generate_blocking (trả dict, không trả JSONResponse), run_8_cases, run_rerender
  quản lý bộ nhớ MPS/CUDA, FIFO GPU worker (gpu_queue, start_gpu_worker)

LƯU Ý: file này phải được import TRƯỚC khi import torch ở nơi khác, vì nó đặt
PYTORCH_ENABLE_MPS_FALLBACK / watermark trước `import torch`.
"""
import os
import queue
from concurrent.futures import Future

# Apple Silicon: cho phép các op chưa hỗ trợ trên MPS tự fallback sang CPU
# (BẮT BUỘC đặt TRƯỚC khi import torch; không ảnh hưởng gì trên NVIDIA GPU)
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

# Tuỳ chọn (Mac): hạ trần allocator MPS để khi thiếu RAM thì báo lỗi OOM "êm" (case đó fail, batch chạy tiếp)
# thay vì kéo máy vào swap và treo. Ví dụ: APP_MPS_WATERMARK=1.0  (KHÔNG đặt 0.0 = bỏ giới hạn, dễ treo máy)
_wm = os.environ.get("APP_MPS_WATERMARK", "").strip()
if _wm:
    try:
        _hi = float(_wm)
        if _hi > 0:
            os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = str(_hi)
            os.environ.setdefault("PYTORCH_MPS_LOW_WATERMARK_RATIO", str(round(_hi * 0.85, 3)))
    except ValueError:
        pass

import asyncio
import colorsys
import gc
import inspect
import io
import json
import math
import random
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageFilter
from diffusers import QwenImage21Pipeline, ZImagePipeline, FlowMatchEulerDiscreteScheduler

# Prompt Enhancer: Qwen-Image-2.1-PE-I2I
try:
    from transformers import AutoProcessor, AutoModelForImageTextToText
    from huggingface_hub import hf_hub_download
except Exception:
    AutoProcessor = None
    AutoModelForImageTextToText = None
    hf_hub_download = None

# Tách nền (tuỳ chọn): pip install rembg onnxruntime
try:
    from rembg import remove as rembg_remove, new_session as rembg_new_session
except Exception:
    rembg_remove = None
    rembg_new_session = None

# Đo swap/RAM hệ thống (tuỳ chọn, nhưng nên có trên Mac): pip install psutil
try:
    import psutil
except Exception:
    psutil = None


# ============================================================
# CONFIG
# ============================================================
APP_VERSION = "4.5-fixed-r4"
# LƯU Ý LICENSE: Qwen-Image-2.1 và LoRA turbo dùng Qwen Research License (nghiên cứu/đánh giá).
# Dùng thương mại cần xin license riêng từ Qwen - đọc file LICENSE trong repo trước khi ra sản phẩm.
QWEN_MODEL_ID = "Qwen/Qwen-Image-2.1"
ZIMAGE_MODEL_ID = "Tongyi-MAI/Z-Image-Turbo"

# ------------------------------------------------------------
# PROMPT ENHANCER
# ------------------------------------------------------------
PROMPT_ENHANCER_MODEL_ID = "Qwen/Qwen-Image-2.1-PE-I2I"
PROMPT_ENHANCER_ENABLED = os.environ.get("APP_PROMPT_ENHANCER", "1") == "1"
# Model card PE-I2I dùng max_new_tokens=24000: PE chạy chế độ thinking, 4096 dễ bị cắt trước khi ra JSON.
PROMPT_ENHANCER_MAX_NEW_TOKENS = int(os.environ.get("APP_PE_MAX_NEW_TOKENS", "24000"))
# Ở chế độ aspect "auto": lấy wh_ratio do PE chọn theo ngữ nghĩa cảnh (đúng thiết kế của tác giả).
PROMPT_ENHANCER_USE_RATIO = os.environ.get("APP_PE_RATIO", "1") == "1"
PROMPT_ENHANCER_TEMPERATURE = float(os.environ.get("APP_PE_TEMPERATURE", "1.0"))
PROMPT_ENHANCER_TOP_P = float(os.environ.get("APP_PE_TOP_P", "0.95"))
PROMPT_ENHANCER_TOP_K = int(os.environ.get("APP_PE_TOP_K", "20"))
PROMPT_ENHANCER_MAX_IMAGE_SIDE = int(os.environ.get("APP_PE_MAX_IMAGE_SIDE", "1536"))
# Nhiều ảnh tham chiếu: mỗi ảnh vào PE tốn token, nên cạnh dài tối đa giảm dần theo số ảnh (tổng ngân sách ~ 1 ảnh 1536 x 3).
PROMPT_ENHANCER_MAX_IMAGES = int(os.environ.get("APP_PE_MAX_IMAGES", "10"))
prompt_enhancer_model = None
prompt_enhancer_processor = None
prompt_enhancer_system_prompt = None
prompt_enhancer_lock = threading.Lock()
# ------------------------------------------------------------
# DEVICE / DTYPE  (tự nhận: NVIDIA CUDA -> Apple MPS -> CPU)
# Có thể ép bằng biến môi trường:
#   APP_DEVICE=cuda|mps|cpu
#   APP_DTYPE=bf16|fp16|fp32
#   APP_CPU_OFFLOAD=1|0        (CUDA: bật model CPU offload, mặc định 1)
#   APP_MPS_MODE=auto|resident|offload
#        auto     : (mặc định) tự chọn theo số liệu thật: trọng số model + APP_MPS_HEADROOM_GB so với
#                   trần bộ nhớ Metal của máy. Đủ chỗ -> resident, không đủ -> offload.
#        resident : pipe.to("mps"), giữ toàn bộ model trên GPU (nhanh, ổn định nếu RAM thừa)
#        offload  : model_cpu_offload, chỉ 1 model lớn nằm trên GPU tại một thời điểm
#   APP_MPS_OFFLOAD=1|0        (tên cũ, vẫn hiểu: 1=offload, 0=resident; APP_MPS_MODE ưu tiên hơn)
#   APP_MPS_HEADROOM_GB=8      (phần RAM chừa cho activation khi auto chọn resident)
#   APP_MPS_WATERMARK=1.0      (hạ trần allocator MPS, xem đầu file)
#   APP_TURBO=1|0              (nạp LoRA turbo cho chế độ Nháp nhanh, mặc định 1)
#   APP_TURBO_WEIGHT=...       (tên file LoRA turbo, mặc định r256; r128 nhẹ hơn)
#   APP_RECYCLE_GROWTH_GB=4    (bộ nhớ GPU tăng quá mức này so với sau case 1 -> nạp lại pipeline; 0=tắt)
#   APP_RECYCLE_EVERY=0        (nạp lại pipeline sau mỗi N case; 0=tắt)
#   APP_STALL_SECONDS=300      (không có tiến triển quá lâu -> UI cảnh báo)
#   APP_MAX_INPUT_SIDE=2048    (thu nhỏ ảnh upload nếu cạnh dài hơn mức này; 0=tắt)
#   APP_PE_MAX_NEW_TOKENS=24000 (trần token của Prompt Enhancer, theo model card)
#   APP_PE_RATIO=1|0           (aspect 'auto': dùng wh_ratio do PE chọn cho từng case; 0=luôn theo ảnh gốc)
#   APP_HOST=127.0.0.1 / APP_PORT=8000   (đặt APP_HOST=0.0.0.0 nếu muốn truy cập từ máy khác trong LAN)
# ------------------------------------------------------------
_DTYPE_TABLE = {
    "bf16": torch.bfloat16, "bfloat16": torch.bfloat16,
    "fp16": torch.float16, "float16": torch.float16,
    "fp32": torch.float32, "float32": torch.float32,
}


def select_device():
    forced = os.environ.get("APP_DEVICE", "").strip().lower()
    if forced == "cuda" and torch.cuda.is_available():
        return "cuda"
    if forced == "mps" and torch.backends.mps.is_available():
        return "mps"
    if forced == "cpu":
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def select_dtype(device):
    forced = os.environ.get("APP_DTYPE", "").strip().lower()
    if forced in _DTYPE_TABLE:
        return _DTYPE_TABLE[forced]
    if device == "cuda":
        # bf16 chạy native từ Ampere (RTX 30xx / A100) trở lên; GPU cũ hơn dùng fp16
        major, _ = torch.cuda.get_device_capability(0)
        return torch.bfloat16 if major >= 8 else torch.float16
    if device == "mps":
        # bf16 trên MPS cần macOS 14+; macOS cũ hơn dùng fp32 cho an toàn
        check = getattr(torch.backends.mps, "is_macos_or_newer", None)
        return torch.bfloat16 if (check and check(14, 0)) else torch.float32
    return torch.float32


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


DEVICE = select_device()
DTYPE = select_dtype(DEVICE)
USE_CPU_OFFLOAD = os.environ.get("APP_CPU_OFFLOAD", "1") == "1"
MPS_HEADROOM_GB = _env_float("APP_MPS_HEADROOM_GB", 8)
RECYCLE_GROWTH_GB = _env_float("APP_RECYCLE_GROWTH_GB", 4)
RECYCLE_EVERY = int(_env_float("APP_RECYCLE_EVERY", 0))
STALL_SECONDS = _env_float("APP_STALL_SECONDS", 300)
SWAP_WARN_GB = 1.5
MAX_INPUT_SIDE = int(_env_float("APP_MAX_INPUT_SIDE", 2048))
GB = 1024 ** 3

print("=" * 60)
print("AI DEVICE:", DEVICE)
print("DTYPE:", DTYPE)

if DEVICE == "mps":
    print("Apple Metal / MPS: ENABLED | mode:", os.environ.get("APP_MPS_MODE", "auto"))

elif DEVICE == "cuda":
    props = torch.cuda.get_device_properties(0)
    print("CUDA: ENABLED")
    print("GPU:", props.name, f"({props.total_memory / 1024**3:.1f} GB VRAM)")
    print("CPU offload (Qwen):", USE_CPU_OFFLOAD)

else:
    print("WARNING: Running on CPU (rất chậm)")

if DEVICE != "cpu" and psutil is None:
    print("Gợi ý: pip install psutil  (để log swap/RAM và cảnh báo khi máy bắt đầu swap)")

print("=" * 60)
BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR = BASE_DIR / "uploads"
BATCH_DIR = OUTPUT_DIR / "product_cases"
for d in (OUTPUT_DIR, UPLOAD_DIR, BATCH_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ============================================================
# GPU FIFO QUEUE
# ============================================================
# Một process chỉ chạy đúng 1 GPU worker. Mọi inference task (single,
# 8-case, rerender) đi qua cùng queue => FIFO và không còn HTTP 409
# chỉ vì GPU đang bận.
gpu_queue = queue.Queue()
gpu_worker_thread = None
gpu_worker_start_lock = threading.Lock()

progress = {"running": False, "step": 0, "total": 0, "percent": 0, "message": "Idle"}
batch_jobs = {}
batch_jobs_lock = threading.Lock()
cancel_events = {}

qwen_pipeline = None
zimage_pipeline = None
current_model = None


# ============================================================
# MEMORY: đo, dọn, log
# ============================================================
def _call(owner, name):
    fn = getattr(owner, name, None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:
        return None


def gpu_used_gb():
    """Bộ nhớ GPU driver đang giữ (kể cả cache). Dùng để phát hiện leak/fragmentation giữa các case."""
    if DEVICE == "mps":
        v = _call(torch.mps, "driver_allocated_memory")
    elif DEVICE == "cuda":
        v = _call(torch.cuda, "memory_reserved")
    else:
        v = None
    return None if v is None else v / GB


def gpu_limit_gb():
    """Trần bộ nhớ GPU: recommended working set của Metal, hoặc tổng VRAM của CUDA."""
    if DEVICE == "mps":
        v = _call(torch.mps, "recommended_max_memory")
    elif DEVICE == "cuda":
        try:
            v = torch.cuda.get_device_properties(0).total_memory
        except Exception:
            v = None
    else:
        v = None
    return None if v is None else v / GB


def swap_used_gb():
    if psutil is None:
        return None
    try:
        return psutil.swap_memory().used / GB
    except Exception:
        return None


def get_memory_info():
    info = {"device": DEVICE}
    pairs = {
        "gpu_used_gb": gpu_used_gb(),
        "gpu_limit_gb": gpu_limit_gb(),
        "swap_used_gb": swap_used_gb(),
    }
    if DEVICE == "mps":
        v = _call(torch.mps, "current_allocated_memory")
        pairs["gpu_tensors_gb"] = None if v is None else v / GB
    elif DEVICE == "cuda":
        v = _call(torch.cuda, "memory_allocated")
        pairs["gpu_tensors_gb"] = None if v is None else v / GB
    if psutil is not None:
        try:
            pairs["ram_available_gb"] = psutil.virtual_memory().available / GB
        except Exception:
            pass
    for k, v in pairs.items():
        if v is not None:
            info[k] = round(v, 2)
    return info


def log_memory(tag):
    print(f"[MEMORY] {tag}: {get_memory_info()}", flush=True)


def free_memory(deep=False):
    """
    Giải phóng RAM/VRAM trên CUDA và Apple MPS.
    LƯU Ý: phải gọi SAU khối except (không phải bên trong), vì khi đang xử lý exception thì traceback
    vẫn giữ tham chiếu tới các frame (kèm tensor trung gian) nên gc không thu hồi được.
    """
    for _ in range(2 if deep else 1):
        gc.collect()
    if DEVICE == "cuda":
        _call(torch.cuda, "synchronize")
        _call(torch.cuda, "empty_cache")
        _call(torch.cuda, "ipc_collect")
    elif DEVICE == "mps":
        _call(torch.mps, "synchronize")
        _call(torch.mps, "empty_cache")


def is_memory_error(exc):
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(k in text for k in ("out of memory", "insufficient memory", "invalid buffer size", "mps backend out"))


# ============================================================
# 8 CASE = MỘT BỘ SƯU TẬP ẢNH (mô tả Ý ĐỊNH, không mô tả sản phẩm)
# ============================================================
COLLECTION_SIZE = 8

CASE_INTENTS = [
("clean_packshot", "Clean Packshot",
 "Create a clean e-commerce packshot of the product on a pure white seamless background, shown fully in frame from a straight-on front view. Even soft studio lighting reveals every detail, with a subtle natural contact shadow beneath the product. The composition is centered with comfortable margins, crisp and neutral, suitable as the main listing image on an online store."),
("hero_brand", "Brand Hero",
 "Create a premium brand hero photograph of the product on an elegant surface that suits its character, against a softly graded background in tones that complement the product's own colors. Soft directional studio lighting shapes refined highlights and a realistic soft shadow beneath the product. The composition is centered, minimal and confident, like the opening image of a product launch campaign."),
("detail_closeup", "Detail Close-up",
 "Create a close-up detail photograph focused on the most distinctive feature of the product, such as its label, logo, material texture, lens, button or finish. Use a shallow depth of field and soft raking light that reveals surface quality and craftsmanship. Every visible part matches the reference image, and any branding stays sharp and readable. The mood is tactile and premium."),
("flat_lay", "Flat Lay",
 "Create a top-down flat lay photograph with the product as the centerpiece, arranged on a clean textured surface in a soft, harmonious palette. Add a few carefully chosen props related to how the product is used, placed with generous spacing so the product stays clearly dominant. Soft even daylight creates gentle natural shadows. The composition is balanced and tidy, made for a social media carousel."),
("lifestyle_context", "Lifestyle Context",
 "Place the product naturally in the real-life setting where this kind of product is normally used, on the surface and in the room that fit it, with a few tasteful details softly blurred in the background. Warm natural window daylight creates realistic soft shadows and subtle reflections, with shallow depth of field while the product stays sharp. The feeling is calm, clean and aspirational."),
("human_in_use", "Human In Use",
 "Show the product being used naturally by one adult person in its normal setting, with only a hand and forearm visible. The hand has realistic skin texture, natural fingers and realistic anatomy, and the person wears neutral-colored clothing. The background is softly blurred, lit by natural window light with subtle fill light, and the product stays sharp and dominant, so its real size and use are easy to understand."),
("concept_benefit", "Concept Benefit",
 "Create a concept photograph that expresses the main benefit or selling point of the product visually. Use props, natural elements, light or motion that tell why someone would want this product, while the product stays clearly dominant and unobstructed. Soft natural light and shallow depth of field give the scene a fresh, believable atmosphere."),
("launch_announcement", "Launch Announcement",
 "Create a bold launch announcement visual for a new product. Position the product slightly off-center in a striking editorial composition, with a confident background color that complements the product and a few simple graphic elements. Dramatic but refined lighting with a side key light and a rim light gives the product presence. Leave generous empty space in the upper and side areas for the launch headline that will be added later. The mood is fresh, exciting and premium."),
]

FALLBACK_INTRO = """Use the product in the reference image as the exact source of truth.{description_sentence} Keep the product exactly as it appears in the reference image: the same shape, proportions, parts, materials, branding, label and original colors, with every visible part of the product faithfully present. Lighting changes only the luminance of the product, never its hue. The scene adapts to the product; the product stays unchanged.

"""

FALLBACK_OUTRO = """

Photorealistic commercial product photography with physically accurate lighting, realistic materials and reflections, and extremely sharp product details. The product is the single dominant subject, and the scene contains only the elements described above. Any text, label or display on the product keeps its original content."""


def build_pe_instruction(intent, index, total, description="", palette_hint="", ref_count=1):
    note = f" Product note from the user: {description}." if description else ""
    if palette_hint:
        note += f" Measured colors of the product in the reference image: {palette_hint}."
    if ref_count > 1:
        note += (f" The {ref_count} reference images show the same single product from different views and details, "
                 f"so use all of them to understand the product.")
    multi_note = MULTI_REF_TAG_CLAUSE if ref_count > 1 else ""
    return (f"{intent} This is shot {index} of {total} in one advertising collection of this same product, "
            f"each shot with a different scene. Identify the actual product from the reference image(s) and choose "
            f"the props, surface, setting and color palette that suit it; the product itself stays exactly as in "
            f"the reference image(s).{multi_note}{note}")


def build_fallback_prompt(intent, description="", color_note="", ref_count=1):
    desc_sentence = f" The product is {description}." if description else ""
    intro = FALLBACK_INTRO.format(description_sentence=desc_sentence)
    if ref_count > 1:
        intro = intro.rstrip() + " " + MULTI_REF_CLAUSE + MULTI_REF_QWEN_CLAUSE + "\n\n"
    if color_note:
        intro = intro.rstrip() + " " + color_note + "\n\n"
    return intro + intent + FALLBACK_OUTRO


def build_case_prompts(description="", color_note="", palette_hint="", ref_count=1):
    description = (description or "").strip().rstrip(".")
    total = len(CASE_INTENTS)
    return [{"slug": slug, "name": name,
             "intent": build_pe_instruction(intent, i + 1, total, description, palette_hint, ref_count),
             "fallback": build_fallback_prompt(intent, description, color_note, ref_count)}
            for i, (slug, name, intent) in enumerate(CASE_INTENTS)]


PRESERVE_CLAUSE = ("Keep the product exactly as in the reference image, with the same shape, parts, "
                   "branding and original colors; lighting may change its luminance but never its hue.")
MULTI_REF_CLAUSE = ("The reference images show the same single product from different views and details; "
                    "the product in the result matches all of them and appears only once.")
MULTI_REF_TAG_CLAUSE = (
    " For multiple references, explicitly refer to each source image as <image1>, <image2>, "
    "<image3>, etc., and use each image for the specific visible details it provides. "
    "The first image is the primary product view; the remaining images are additional views/details "
    "of the same product. Do not invent a second product."
)
MULTI_REF_QWEN_CLAUSE = (
    " Use all supplied reference images as condition images. They are different views of the same single product; "
    "<image1> is the primary view and <image2>, <image3>, etc. provide additional visible details. "
    "Synthesize one product only and do not duplicate the product in the scene."
)

# ============================================================
# PROMPT ENHANCER — Qwen-Image-2.1-PE-I2I
# ============================================================
def _pe_result(original, **extra):
    base = {"rewritten_prompt": original or "", "wh_ratio": "", "ratio_follow": "",
            "thinking": "", "raw": "", "parse_ok": False}
    base.update(extra)
    return base


def get_prompt_enhancer():
    global prompt_enhancer_model, prompt_enhancer_processor, prompt_enhancer_system_prompt
    if not PROMPT_ENHANCER_ENABLED:
        return None, None, None
    if AutoProcessor is None or AutoModelForImageTextToText is None or hf_hub_download is None:
        raise RuntimeError("Prompt Enhancer dependencies missing. Install transformers>=5.17 accelerate huggingface_hub.")
    if prompt_enhancer_model is not None:
        return prompt_enhancer_model, prompt_enhancer_processor, prompt_enhancer_system_prompt

    print("=" * 60)
    print("Loading Qwen-Image-2.1 Prompt Enhancer...")
    print("=" * 60)
    prompt_enhancer_processor = AutoProcessor.from_pretrained(PROMPT_ENHANCER_MODEL_ID)
    system_path = hf_hub_download(PROMPT_ENHANCER_MODEL_ID, "system_prompt.txt")
    prompt_enhancer_system_prompt = Path(system_path).read_text(encoding="utf-8").strip()

    if DEVICE == "cuda":
        prompt_enhancer_model = AutoModelForImageTextToText.from_pretrained(
            PROMPT_ENHANCER_MODEL_ID, dtype=DTYPE, device_map="auto")
    elif DEVICE == "mps":
        prompt_enhancer_model = AutoModelForImageTextToText.from_pretrained(
            PROMPT_ENHANCER_MODEL_ID, dtype=DTYPE).to("mps")
    else:
        prompt_enhancer_model = AutoModelForImageTextToText.from_pretrained(
            PROMPT_ENHANCER_MODEL_ID, dtype=torch.float32).to("cpu")
    prompt_enhancer_model.eval()
    log_memory("AFTER PE LOAD")
    return prompt_enhancer_model, prompt_enhancer_processor, prompt_enhancer_system_prompt


def _drop_model_hooks(model):
    """Best-effort removal of Accelerate/Diffusers hooks before dropping a model."""
    if model is None:
        return
    for name in ("remove_all_hooks", "_hf_hook"):
        obj = getattr(model, name, None)
        if name == "remove_all_hooks" and callable(obj):
            try:
                obj()
            except Exception:
                pass
    hook = getattr(model, "_hf_hook", None)
    if hook is not None:
        remover = getattr(hook, "remove", None)
        if callable(remover):
            try:
                remover()
            except Exception:
                pass


def unload_prompt_enhancer():
    global prompt_enhancer_model, prompt_enhancer_processor, prompt_enhancer_system_prompt
    model = prompt_enhancer_model
    if model is None and prompt_enhancer_processor is None:
        return
    print("[PE] Unloading Prompt Enhancer...", flush=True)
    try:
        if model is not None:
            _drop_model_hooks(model)
            if DEVICE in ("mps", "cuda"):
                try:
                    model.to("cpu")
                except Exception as exc:
                    print(f"[PE] model.to(cpu) skipped: {exc}", flush=True)
    except Exception as exc:
        print(f"[PE] unload warning: {exc}", flush=True)
    prompt_enhancer_model = None
    prompt_enhancer_processor = None
    prompt_enhancer_system_prompt = None
    del model
    free_memory(deep=True)
    log_memory("AFTER PE UNLOAD")


def parse_prompt_enhancer_output(text):
    if not text:
        return _pe_result("")
    raw = text.strip()
    thinking, answer = "", raw
    if "</think>" in raw:
        thinking, answer = raw.split("</think>", 1)
        thinking = thinking.replace("<think>", "").strip()
        answer = answer.strip()

    candidates = [answer] + re.findall(r"\{[\s\S]*\}", answer)
    parsed = None
    for candidate in reversed(candidates):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                parsed = obj
                break
        except Exception:
            pass
    if parsed is None:
        return _pe_result("", thinking=thinking, raw=raw)
    rewritten = str(parsed.get("rewritten_prompt", "")).strip()
    if not rewritten:
        return _pe_result("", thinking=thinking, raw=raw,
                          wh_ratio=str(parsed.get("wh_ratio", "")).strip(),
                          ratio_follow=str(parsed.get("ratio_follow", "")).strip())
    return {"rewritten_prompt": rewritten,
            "wh_ratio": str(parsed.get("wh_ratio", "")).strip(),
            "ratio_follow": str(parsed.get("ratio_follow", "")).strip(),
            "thinking": thinking, "raw": raw, "parse_ok": True}


def _as_image_list(input_image):
    if input_image is None:
        return []
    return list(input_image) if isinstance(input_image, (list, tuple)) else [input_image]


def enhance_prompt(user_prompt, input_image, cancel=None, aspect_hint=None, color_note=""):
    """input_image: một ảnh PIL hoặc list ảnh tham chiếu (tối đa 10, ảnh đầu là ảnh chính)."""
    images_in = _as_image_list(input_image)[:max(1, PROMPT_ENHANCER_MAX_IMAGES)]
    n_refs = len(images_in)
    if not PROMPT_ENHANCER_ENABLED or not images_in:
        return _pe_result(user_prompt, enhancer_disabled=not PROMPT_ENHANCER_ENABLED)
    if cancel is not None and cancel.is_set():
        raise GenerationCancelled()

    with prompt_enhancer_lock:
        try:
            if cancel is not None and cancel.is_set():
                raise GenerationCancelled()
            model, processor, system_prompt = get_prompt_enhancer()
            max_side = PROMPT_ENHANCER_MAX_IMAGE_SIDE
            if max_side > 0 and n_refs > 3:
                max_side = max(512, int(max_side * 3 / n_refs))   # nhiều ảnh: thu nhỏ để giữ bộ nhớ/token ổn định
            pe_images = []
            for im_in in images_in:
                im = im_in.convert("RGB")
                if max_side > 0 and max(im.size) > max_side:
                    scale = max_side / max(im.size)
                    im = im.resize((max(1, int(im.width * scale)),
                                    max(1, int(im.height * scale))), Image.Resampling.LANCZOS)
                pe_images.append(im)

            size_hint = f"\n\nOutput aspect ratio: {aspect_hint}." if aspect_hint else ""
            messages = [
                {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
                {"role": "user", "content": [
                    *[{"type": "image", "image": im} for im in pe_images],
                    {"type": "text", "text": (user_prompt or "") + size_hint},
                ]},
            ]
            
            # PE-I2I cần chat template xử lý cả placeholder ảnh + token văn bản trong cùng một lượt.
            # Không tách apply_chat_template(tokenize=False) rồi gọi processor lần 2, vì cách đó
            # có thể làm lệch image-token mapping trên các phiên bản Transformers khác nhau.
            try:
                inputs = processor.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                    enable_thinking=True,
                )
            except TypeError:
                # Tương thích với Transformers/processor cũ không có enable_thinking.
                inputs = processor.apply_chat_template(
                    messages,
                    tokenize=True,
                    add_generation_prompt=True,
                    return_dict=True,
                    return_tensors="pt",
                )
            target = model.device if DEVICE == "cuda" else torch.device(DEVICE)
            inputs = {k: (v.to(target) if hasattr(v, "to") else v) for k, v in inputs.items()}

            with torch.inference_mode():
                output_ids = model.generate(
                    **inputs, max_new_tokens=PROMPT_ENHANCER_MAX_NEW_TOKENS,
                    do_sample=True, temperature=PROMPT_ENHANCER_TEMPERATURE,
                    top_p=PROMPT_ENHANCER_TOP_P, top_k=PROMPT_ENHANCER_TOP_K)

            if cancel is not None and cancel.is_set():
                raise GenerationCancelled()

            input_len = inputs["input_ids"].shape[1]
            generated = processor.tokenizer.decode(
                output_ids[0, input_len:], skip_special_tokens=True)
            print(f"[PE] generated_tokens={max(0, output_ids.shape[1] - input_len)}", flush=True)
            result = parse_prompt_enhancer_output(generated)
            del output_ids, inputs, pe_images
            free_memory()
            if not result.get("parse_ok") or not result.get("rewritten_prompt"):
                print("[PE] Không parse được JSON -> dùng prompt gốc", flush=True)
                return _pe_result(user_prompt, error="PE output không parse được (có thể bị cắt do max_new_tokens)",
                                  raw=(result.get("raw") or "")[-1500:])
            result["rewritten_prompt"] = (" ".join(result["rewritten_prompt"].split()) + " " + PRESERVE_CLAUSE
                                          + ((" " + MULTI_REF_CLAUSE) if n_refs > 1 else "")
                                          + ((" " + color_note) if color_note else ""))
            print("[PE] parse_ok=", result["parse_ok"], "wh_ratio=", result.get("wh_ratio"),
                  "ratio_follow=", result.get("ratio_follow"),
                  "prompt=", result["rewritten_prompt"][:700])
            return result
        except GenerationCancelled:
            free_memory(deep=True)
            raise
        except Exception as exc:
            traceback.print_exc()
            free_memory(deep=True)
            return _pe_result(user_prompt, error=f"{type(exc).__name__}: {exc}")


def enhance_case_prompts(cases, input_image, job_id=None, cancel=None, aspect_hint=None, color_note=""):
    enhanced, total = [], len(cases)
    for index, case in enumerate(cases):
        if cancel is not None and cancel.is_set():
            raise GenerationCancelled()
        name = case["name"]
        if job_id:
            update_batch(job_id, case=index + 1, total=total,
                         percent=int(index / max(1, total) * 20),
                         message=f"Enhancing prompt {index + 1}/{total}: {name}", persist=True)
        result = enhance_prompt(case["intent"], input_image, cancel=cancel, aspect_hint=aspect_hint, color_note=color_note)
        if cancel is not None and cancel.is_set():
            raise GenerationCancelled()
        used_pe = bool(result.get("parse_ok") and result.get("rewritten_prompt"))
        final_prompt = result["rewritten_prompt"] if used_pe else case["fallback"]
        enhanced.append({
            "slug": case["slug"], "name": name, "prompt": final_prompt,
            "original_prompt": case["intent"], "enhanced_prompt": final_prompt,
            "fallback_prompt": case["fallback"],
            "prompt_enhancer": {
                "used": used_pe,
                "parse_ok": result.get("parse_ok", False),
                "wh_ratio": result.get("wh_ratio", ""),
                "ratio_follow": result.get("ratio_follow", ""),
                "error": result.get("error"),
            },
        })
    return enhanced


def save_prompt_manifest(case_dir, cases):
    payload = [{
        "slug": c["slug"], "name": c["name"],
        "original_prompt": c.get("original_prompt", c["prompt"]),
        "enhanced_prompt": c.get("enhanced_prompt", c["prompt"]),
        "fallback_prompt": c.get("fallback_prompt", ""),
        "prompt_enhancer": c.get("prompt_enhancer", {}),
    } for c in cases]
    path = case_dir / "prompts.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_prompt_manifest(case_dir):
    path = case_dir / "prompts.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return None
        normalized = []
        for item in data:
            if not isinstance(item, dict):
                continue
            original = item.get("original_prompt", "")
            enhanced = item.get("enhanced_prompt") or item.get("prompt") or original
            normalized.append({
                **item,
                "original_prompt": original,
                "enhanced_prompt": enhanced,
                "prompt": enhanced,
            })
        return normalized
    except Exception:
        return None


# ============================================================
# MODEL LOADING
# ============================================================
TURBO_ENABLED = os.environ.get("APP_TURBO", "1") == "1"
TURBO_REPO = "Viggle/Qwen-Image-2.1-viggle-turbo"
TURBO_WEIGHT = os.environ.get("APP_TURBO_WEIGHT", "Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r256.safetensors")
TURBO_STEPS = 6
TURBO_SIGMAS = [1.0, 0.9375, 0.875, 0.75, 0.5, 0.25]

qwen_state = {"turbo_loaded": False, "turbo_error": None, "base_scheduler": None, "turbo_scheduler": None}


def reset_qwen_state():
    qwen_state.update(turbo_loaded=False, turbo_error=None, base_scheduler=None, turbo_scheduler=None)


def prepare_turbo(pipe):
    qwen_state["base_scheduler"] = pipe.scheduler
    qwen_state["turbo_scheduler"] = FlowMatchEulerDiscreteScheduler.from_config(pipe.scheduler.config, shift_terminal=None)
    if not TURBO_ENABLED:
        qwen_state["turbo_error"] = "APP_TURBO=0"
        return
    try:
        print(f"Loading turbo LoRA: {TURBO_REPO} / {TURBO_WEIGHT}")
        pipe.load_lora_weights(TURBO_REPO, weight_name=TURBO_WEIGHT, adapter_name="turbo")
        pipe.disable_lora()
        qwen_state["turbo_loaded"] = True
    except Exception as exc:
        qwen_state["turbo_error"] = f"{type(exc).__name__}: {exc}"
        print("WARNING: không nạp được turbo LoRA (cần `pip install peft`, diffusers đủ mới):", exc)


def set_qwen_mode(pipe, turbo):
    if turbo and qwen_state["turbo_loaded"]:
        pipe.enable_lora()
        pipe.scheduler = qwen_state["turbo_scheduler"]
        return True
    if qwen_state["turbo_loaded"]:
        pipe.disable_lora()
    pipe.scheduler = qwen_state["base_scheduler"]
    return False


def qwen_sampling_kwargs(turbo, steps):
    if turbo:
        return {"num_inference_steps": TURBO_STEPS, "sigmas": TURBO_SIGMAS, "true_cfg_scale": 1.0}
    return {"num_inference_steps": steps}


def _drop_pipeline(pipe):
    if pipe is None:
        return
    fn = getattr(pipe, "remove_all_hooks", None)
    if callable(fn):
        try:
            fn()
        except Exception:
            pass


def unload_qwen_pipeline():
    """Unload only Qwen; used by QwenSession.recycle so unrelated state is not disturbed."""
    global qwen_pipeline, current_model
    if qwen_pipeline is not None:
        print("[MODEL] Unloading Qwen pipeline...", flush=True)
        _drop_pipeline(qwen_pipeline)
        qwen_pipeline = None
    if current_model == "qwen-2.1":
        current_model = None
    reset_qwen_state()
    free_memory(deep=True)
    log_memory("AFTER QWEN UNLOAD")


def unload_models():
    global qwen_pipeline, zimage_pipeline, current_model
    print("[MODEL] Unloading pipelines...")
    _drop_pipeline(qwen_pipeline)
    _drop_pipeline(zimage_pipeline)
    qwen_pipeline = None
    zimage_pipeline = None
    current_model = None
    reset_qwen_state()
    free_memory(deep=True)
    log_memory("AFTER UNLOAD")


def load_pipeline(pipeline_cls, model_id):
    # SỬA LỖI (Kỹ thuật #1): Bắt buộc dùng `torch_dtype` theo chuẩn diffusers.
    # Việc dùng `dtype` sẽ khiến mô hình bị nạp ngầm bằng float32 gây tràn RAM.
    return pipeline_cls.from_pretrained(model_id, torch_dtype=DTYPE)


def model_weight_bytes(pipe):
    total = 0
    for comp in getattr(pipe, "components", {}).values():
        if isinstance(comp, torch.nn.Module):
            total += sum(p.numel() * p.element_size() for p in comp.parameters())
            total += sum(b.numel() * b.element_size() for b in comp.buffers())
    return total


def choose_mps_mode(pipe):
    forced = os.environ.get("APP_MPS_MODE", "").strip().lower()
    if forced in ("resident", "offload"):
        return forced, "APP_MPS_MODE"
    legacy = os.environ.get("APP_MPS_OFFLOAD", "").strip()
    if legacy in ("0", "1"):
        return ("offload" if legacy == "1" else "resident"), "APP_MPS_OFFLOAD"
    limit = gpu_limit_gb()
    if limit is None:
        return "offload", "không đọc được trần bộ nhớ Metal -> chọn an toàn"
    weights_gb = model_weight_bytes(pipe) / GB
    need = weights_gb + MPS_HEADROOM_GB
    verdict = "<=" if need <= limit else ">"
    reason = f"trọng số {weights_gb:.1f} GB + headroom {MPS_HEADROOM_GB:.0f} GB {verdict} trần Metal {limit:.1f} GB"
    return ("resident" if need <= limit else "offload"), reason


def place_pipeline(pipe, allow_offload):
    if DEVICE == "cuda":
        if allow_offload and USE_CPU_OFFLOAD:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to("cuda")
    elif DEVICE == "mps":
        if allow_offload:
            mode, reason = choose_mps_mode(pipe)
            print(f"[MPS] mode = {mode} ({reason})")
        else:
            mode = "resident"
            
        if mode == "offload":
            try:
                pipe.enable_model_cpu_offload(device="mps")
            except TypeError:  
                # SỬA LỖI (Kỹ thuật #3): Gọi offload mặc định thay vì ép to("mps") gây sập bộ nhớ
                try:
                    pipe.enable_model_cpu_offload()
                except Exception as e:
                    print(f"Warning: Offload failed, keeping on CPU to prevent OOM. Error: {e}")
        else:
            pipe.to("mps")
            
        try:
            pipe.enable_vae_tiling()
        except Exception:
            pass
    return pipe


def get_qwen_pipeline():
    global qwen_pipeline, current_model
    if qwen_pipeline is not None:
        current_model = "qwen-2.1"
        return qwen_pipeline
    unload_models()
    print("=" * 60)
    print("Loading Qwen-Image-2.1...")
    print("=" * 60)
    pipe = load_pipeline(QwenImage21Pipeline, QWEN_MODEL_ID)
    prepare_turbo(pipe)  # nạp LoRA trước khi bật offload
    qwen_pipeline = place_pipeline(pipe, allow_offload=True)
    current_model = "qwen-2.1"
    print(f"Qwen-Image-2.1 loaded on {DEVICE}.")
    log_memory("AFTER LOAD")
    return qwen_pipeline


def get_zimage_pipeline():
    global zimage_pipeline, current_model
    if zimage_pipeline is not None:
        current_model = "zimage"
        return zimage_pipeline
    unload_models()
    print("=" * 60)
    print("Loading Z-Image-Turbo...")
    print("=" * 60)
    zimage_pipeline = place_pipeline(load_pipeline(ZImagePipeline, ZIMAGE_MODEL_ID), allow_offload=False)
    current_model = "zimage"
    print(f"Z-Image-Turbo loaded on {DEVICE}.")
    log_memory("AFTER LOAD")
    return zimage_pipeline


# ============================================================
# HELPERS
# ============================================================
QWEN_NATIVE_SIZES = {
    "1:1": (2048, 2048),
    "4:3": (2400, 1792),
    "3:4": (1792, 2400),
    "3:2": (2528, 1696),
    "2:3": (1696, 2528),
    "16:9": (2752, 1536),
    "9:16": (1536, 2752),
}


def get_dimensions(aspect_ratio, resolution):
    nw, nh = QWEN_NATIVE_SIZES.get(aspect_ratio, QWEN_NATIVE_SIZES["1:1"])
    if resolution >= 2048:
        return nw, nh
    k = resolution / 2048
    return max(16, round(nw * k / 16) * 16), max(16, round(nh * k / 16) * 16)


def get_dimensions_long_side(aspect_ratio, resolution):
    ratios = {"1:1": (1, 1), "4:3": (4, 3), "3:4": (3, 4), "3:2": (3, 2), "2:3": (2, 3), "16:9": (16, 9), "9:16": (9, 16)}
    rw, rh = ratios.get(aspect_ratio, (1, 1))
    if rw == rh:
        w = h = resolution
    elif rw > rh:
        w, h = resolution, int(resolution * rh / rw)
    else:
        h, w = resolution, int(resolution * rw / rh)
    return max(16, (w // 16) * 16), max(16, (h // 16) * 16)


def make_generator(seed):
    if seed is None or seed < 0:
        return None
    # MPS: dùng CPU generator để tương thích rộng hơn với diffusers/PyTorch và giữ seed ổn định.
    # CUDA dùng generator CUDA; CPU dùng generator CPU.
    gen_device = "cuda" if DEVICE == "cuda" else "cpu"
    g = torch.Generator(device=gen_device)
    g.manual_seed(seed)
    return g


ASPECT_RATIOS = {"1:1": 1.0, "4:3": 4/3, "3:4": 3/4, "3:2": 3/2, "2:3": 2/3, "16:9": 16/9, "9:16": 9/16}


def resolve_aspect(aspect_ratio, ref_image=None):
    if aspect_ratio in ASPECT_RATIOS:
        return aspect_ratio
    if aspect_ratio == "auto" and ref_image is not None and ref_image.height > 0:
        r = ref_image.width / ref_image.height
        return min(ASPECT_RATIOS, key=lambda k: abs(math.log(r / ASPECT_RATIOS[k])))
    return "1:1"


def normalize_ratio(text):
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[:/xX]\s*(\d+(?:\.\d+)?)\s*$", text or "")
    if not m:
        return None
    w, h = float(m.group(1)), float(m.group(2))
    if w <= 0 or h <= 0:
        return None
    r = w / h
    return min(ASPECT_RATIOS, key=lambda k: abs(math.log(r / ASPECT_RATIOS[k])))


def pick_case_ratio(aspect_ratio, aspect_auto, pe_meta):
    if aspect_auto and PROMPT_ENHANCER_USE_RATIO and pe_meta and pe_meta.get("parse_ok"):
        r = normalize_ratio(pe_meta.get("wh_ratio"))
        if r:
            return r
    return aspect_ratio


RGBA_PREFIX = "This is an RGBA image with transparency. "
RGBA_SUFFIX = " The image has alpha channel and the background is transparent."
MAX_REFERENCE_IMAGES = 10


def build_rgba_prompt(prompt):
    p = (prompt or "").strip()
    if "RGBA" in p.upper():
        return p
    if p and p[-1] not in ".!?":
        p += "."
    return RGBA_PREFIX + p + RGBA_SUFFIX


COLOR_LOCK_ENABLED = os.environ.get("APP_COLOR_LOCK", "1") == "1"


def _color_name(h, s, v):
    if v < 0.20:
        return "black"
    if s < 0.10:
        if v > 0.88:
            return "white"
        if v > 0.62:
            return "light gray"
        return "gray" if v > 0.35 else "dark gray"
    pastel = s < 0.32 and v > 0.72
    if h < 12 or h >= 330:
        return "vivid pink" if (h >= 300 and s > 0.5) else ("soft pink" if pastel else ("pink" if (s < 0.6 and v > 0.6) else ("deep red" if v < 0.45 else "red")))
    if h < 50:
        if pastel:
            return "beige"
        if s < 0.32:
            return "taupe"
        return "brown" if v < 0.75 else ("vivid orange" if (s > 0.6 and v > 0.6) else "orange")
    if h < 68:
        base = "yellow"
    elif h < 165:
        base = "green"
    elif h < 200:
        base = "teal"
    elif h < 255:
        base = "blue"
    elif h < 300:
        base = "purple"
    else:
        base = "pink"
    if s < 0.35 and v > 0.78:
        tone = "light "
    elif s < 0.35:
        tone = "soft "
    elif s > 0.6 and v > 0.6:
        tone = "vivid "
    elif v < 0.45:
        tone = "deep "
    else:
        tone = ""
    return tone + base


_COLOR_CONFUSION = {"brown": "orange", "pink": "red", "beige": "white", "taupe": "gray"}


def _rgb_to_lab(rgb):
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = (lin @ m.T) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], axis=1)


def _kmeans(x, k, iters=15, seed=0):
    rng = np.random.default_rng(seed)
    centers = [x[rng.integers(len(x))]]
    for _ in range(1, k):
        d = np.min(((x[:, None, :] - np.array(centers)[None]) ** 2).sum(-1), axis=1)
        centers.append(x[rng.choice(len(x), p=d / d.sum())] if d.sum() > 0 else x[rng.integers(len(x))])
    centers = np.array(centers)
    labels = np.zeros(len(x), dtype=int)
    for _ in range(iters):
        labels = ((x[:, None, :] - centers[None]) ** 2).sum(-1).argmin(1)
        for i in range(k):
            if (labels == i).any():
                centers[i] = x[labels == i].mean(0)
    return centers, labels


def product_palette(image, max_colors=4, min_share=0.05):
    images = list(image) if isinstance(image, (list, tuple)) else [image]
    if len(images) == 1:
        return _palette_from_pixels(_foreground_pixels(images[0]), max_colors, min_share)
    parts = [px for px in (_foreground_pixels(im) for im in images) if px is not None and len(px)]
    if not parts:
        return []
    per = max(1, 6000 // len(parts))
    px = np.concatenate([q[:: max(1, len(q) // per)] for q in parts])
    return _palette_from_pixels(px, max_colors, min_share)


def _foreground_pixels(image):
    try:
        im = image.convert("RGB")
        im.thumbnail((200, 200))
        a = np.asarray(im, dtype=np.float32)
        h, w, _ = a.shape
        t = max(2, int(0.03 * min(h, w)))
        ring = np.concatenate([a[:t].reshape(-1, 3), a[-t:].reshape(-1, 3),
                               a[:, :t].reshape(-1, 3), a[:, -t:].reshape(-1, 3)])
        bg = np.median(ring, axis=0)
        flat_bg = float(ring.std(axis=0).max()) < 4
        mask = np.abs(a - bg).max(-1) > (6 if flat_bg else 32)
        eroded = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))
        mask = np.asarray(eroded) > 0
        if mask.mean() < 0.02:
            return None
        px = a[mask] / 255.0
        return px[:: max(1, len(px) // 6000)]
    except Exception as exc:
        print("[COLOR] không tách được điểm ảnh sản phẩm:", exc)
        return None


def _palette_from_pixels(px, max_colors=4, min_share=0.05):
    if px is None or len(px) < 20:
        return []
    try:
        k = min(6, len(px))
        centers, labels = _kmeans(_rgb_to_lab(px), k)
        merged = {}
        for i in range(k):
            sel = px[labels == i]
            if len(sel) == 0:
                continue
            hh, ss, vv = colorsys.rgb_to_hsv(*sel.mean(0))
            name = _color_name(hh * 360, ss, vv)
            acc = merged.setdefault(name, [0, np.zeros(3)])
            acc[0] += len(sel)
            acc[1] += sel.sum(0)
        total = sum(v[0] for v in merged.values())
        palette = []
        for name, (n, ssum) in merged.items():
            share = n / total
            if share < min_share:
                continue
            r, g, b = (ssum / n)
            palette.append((name, "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255)), share))
        palette.sort(key=lambda c: -c[2])
        return palette[:max_colors]
    except Exception as exc:
        print("[COLOR] không đo được bảng màu:", exc)
        return []


def palette_sentence(palette):
    items = []
    for name, hexv, share in palette:
        last = name.split()[-1]
        alt = _COLOR_CONFUSION.get(last)
        extra = f", not {alt}" if alt else ""
        items.append(f"{name} ({hexv}, about {int(round(share * 20)) * 5}% of the product{extra})")
    return ", ".join(items)


def make_color_note(image, palette=None):
    if not COLOR_LOCK_ENABLED:
        return ""
    palette = palette if palette is not None else product_palette(image)
    if not palette:
        return ""
    return (f"Exact color inventory of the product in the reference image: {palette_sentence(palette)}. "
            f"Every colored part keeps its own exact color from the reference; no part shifts to another hue, tint "
            f"or metallic finish, and the colors of the background, props, light or reflections never tint the product.")


_rembg_session = None


def get_rembg_session():
    global _rembg_session
    if _rembg_session is None:
        _rembg_session = rembg_new_session(os.environ.get("APP_REMBG_MODEL", "isnet-general-use"))
    return _rembg_session


def prepare_reference(image, remove_bg, target_aspect=None):
    """
    Chuẩn hoá ảnh reference dùng chung cho single-generation và 8-case.

    QUAN TRỌNG:
    - Khi remove_bg=True, Qwen phải nhận chính ảnh đã qua rembg/crop/padding.
    - Không trả RGBA trực tiếp cho Qwen ở đây: Qwen Image Editing nhận condition image
      như một ảnh PIL thông thường; alpha của ảnh input không được coi là một cơ chế
      transparent-output riêng. Vì vậy reference condition được flatten về RGB trên nền
      trung tính sau khi đã tách nền.
    - Transparent mode sẽ thêm chỉ thị RGBA vào prompt và có hậu xử lý alpha cho output
      nếu pipeline trả RGB.
    """
    image = image.convert("RGB")
    if not remove_bg:
        return image, "Using original image"
    if rembg_remove is None:
        return image, "rembg chưa cài (pip install rembg onnxruntime) - dùng ảnh gốc"
    try:
        rgba = rembg_remove(image, session=get_rembg_session()).convert("RGBA")
        bbox = rgba.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
        if bbox is None:
            return image, "Không tách được sản phẩm - dùng ảnh gốc"
        rgba = rgba.crop(bbox)
        w, h = rgba.size
        pad = int(max(w, h) * 0.12)
        cw, ch = w + 2 * pad, h + 2 * pad
        target = float(target_aspect or (image.width / max(1, image.height)))
        if target <= 0:
            target = image.width / max(1, image.height)
        if cw / ch < target:
            cw = int(round(ch * target))
        else:
            ch = int(round(cw / target))

        # Giữ alpha chỉ trong bước xử lý/crop. Condition image đưa vào Qwen là RGB
        # với nền trung tính, để hành vi single Transparent và 8-case nhất quán.
        canvas = Image.new("RGB", (cw, ch), (242, 242, 242))
        canvas.paste(rgba, ((cw - w) // 2, (ch - h) // 2), rgba)
        return canvas, "Background removed + cropped + padded"
    except Exception as exc:
        return image, f"Tách nền lỗi ({exc}) - dùng ảnh gốc"


def prepare_reference_set(images, remove_bg):
    """Prepare all references with one shared canvas aspect ratio.

    Multi-reference Qwen accepts a list of condition images, but using different canvas
    ratios makes the visual framing inconsistent. The first reference defines the canvas
    aspect; every other reference is padded (never stretched/cropped) to that same ratio.
    """
    refs = list(images or [])
    if not refs:
        return [], []
    first = refs[0].convert("RGB")
    target_aspect = first.width / max(1, first.height)
    prepared, notes = [], []
    for src in refs:
        out, note = prepare_reference(src, remove_bg, target_aspect=target_aspect)
        prepared.append(out)
        notes.append(note)
    return prepared, notes


def postprocess_transparent_output(image):
    """
    Đảm bảo output của mode Transparent có alpha nếu pipeline trả RGB.

    Qwen có thể trả RGB dù prompt yêu cầu RGBA. Nếu rembg có mặt, dùng nó như
    fallback hậu xử lý để tạo alpha thực tế thay vì chỉ cảnh báo người dùng.
    Nếu pipeline đã trả RGBA thì giữ nguyên.
    """
    if image is None:
        return image, "no image"
    if image.mode == "RGBA":
        return image, None
    if rembg_remove is None:
        return image, "Pipeline trả ảnh RGB và rembg chưa cài nên chưa thể tạo alpha."
    try:
        out = rembg_remove(image.convert("RGB"), session=get_rembg_session()).convert("RGBA")
        bbox = out.getchannel("A").point(lambda a: 255 if a > 16 else 0).getbbox()
        if bbox is None:
            return image, "Hậu xử lý transparent không tách được foreground; giữ output RGB."
        return out, None
    except Exception as exc:
        return image, f"Hậu xử lý alpha lỗi ({exc}); giữ output {image.mode}."


REF_NAME_PRIMARY = "00_reference.png"


def reference_filename(index):
    return REF_NAME_PRIMARY if index == 0 else f"00_reference_{index + 1:02d}.png"


def load_references(case_dir):
    files = [case_dir / REF_NAME_PRIMARY] + sorted(case_dir.glob("00_reference_*.png"))
    return [Image.open(f).convert("RGB") for f in files if f.is_file()][:MAX_REFERENCE_IMAGES]


def supports_step_callback(pipe):
    try:
        return "callback_on_step_end" in inspect.signature(pipe.__call__).parameters
    except (TypeError, ValueError):
        return False


def make_simple_progress(total, label):
    counter = {"n": 0}

    def cb(*args):
        counter["n"] += 1
        n = counter["n"]
        progress.update({"running": True, "step": n, "total": total,
                         "percent": min(99, int(n / max(1, total) * 100)), "message": f"{label}: bước {n}/{total}"})
        return args[-1] if args and isinstance(args[-1], dict) else {}

    return cb


# ============================================================
# JOB STORE
# ============================================================
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
ACTIVE_STATES = {"queued", "running", "enhancing_prompts", "cancelling"}
TERMINAL_STATES = {"completed", "failed", "cancelled", "interrupted"}


def safe_file(base, *parts):
    if not parts or any((p in (".", "..")) or not FILE_RE.match(p) for p in parts):
        return None
    base = base.resolve()
    path = base.joinpath(*parts).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path if path.is_file() else None


def _persist_job(job_id, job):
    try:
        d = BATCH_DIR / job_id
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, d / "job.json")
    except Exception as exc:
        print(f"[JOB {job_id}] không ghi được job.json: {exc}")


def update_batch(job_id, persist=False, **kwargs):
    with batch_jobs_lock:
        job = batch_jobs[job_id]
        job.update(kwargs)
        job["last_tick"] = time.time()
        if persist:
            _persist_job(job_id, job)


def get_job(job_id):
    if not JOB_ID_RE.match(job_id or ""):
        return None
    with batch_jobs_lock:
        job = batch_jobs.get(job_id)
        if job is not None:
            return dict(job)
    path = BATCH_DIR / job_id / "job.json"
    if not path.is_file():
        return None
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if job.get("status") in ACTIVE_STATES:
        job["status"] = "interrupted"
        job["message"] = "Job bị gián đoạn (server restart hoặc bị kill). Các ảnh đã xong vẫn dùng được."
    job["last_tick"] = time.time()
    with batch_jobs_lock:
        job = batch_jobs.setdefault(job_id, job)
        return dict(job)


class GenerationCancelled(Exception):
    pass


class StepReporter:
    def __init__(self, job_id, cancel, label, done_before, case_total, base_percent=0, span_percent=100):
        self.job_id, self.cancel, self.label = job_id, cancel, label
        self.done_before, self.case_total = done_before, case_total
        self.base_percent, self.span_percent = base_percent, span_percent
        self.total, self.step, self.t0 = 1, 0, time.time()

    def start(self, total_steps):
        self.total, self.step, self.t0 = max(1, int(total_steps)), 0, time.time()
        self._push(f"{self.label}: mã hoá prompt + ảnh tham chiếu...")

    def _push(self, message):
        frac = (self.done_before + self.step / self.total) / self.case_total
        pct = min(99, int(self.base_percent + frac * self.span_percent))
        update_batch(self.job_id, percent=pct, step=self.step, steps_total=self.total, message=message)
        progress.update({"running": True, "step": self.done_before, "total": self.case_total,
                         "percent": pct, "message": message})

    def __call__(self, *args):
        if self.cancel.is_set():
            raise GenerationCancelled()
        self.step += 1
        self._push(f"{self.label}: bước {self.step}/{self.total} · {time.time() - self.t0:.0f}s")
        return args[-1] if args and isinstance(args[-1], dict) else {}


class QwenSession:
    def __init__(self, draft, notify=None):
        self.draft = draft
        self.notify = notify or (lambda msg: None)
        self.pipe = get_qwen_pipeline()
        self.turbo = set_qwen_mode(self.pipe, draft)
        self.steady_gb = None
        self.cases_since_load = 0

    def recycle(self, reason):
        msg = f"Nạp lại Qwen để giải phóng bộ nhớ ({reason})"
        print("=" * 60 + f"\n[RECYCLE] {msg}\n" + "=" * 60, flush=True)
        self.notify(msg)
        old_pipe = self.pipe
        self.pipe = None
        _drop_pipeline(old_pipe)
        del old_pipe
        unload_qwen_pipeline()
        free_memory(deep=True)
        self.pipe = get_qwen_pipeline()
        if self.pipe is None:
            raise RuntimeError("Không thể nạp lại Qwen pipeline sau recycle.")
        self.turbo = set_qwen_mode(self.pipe, self.draft)
        self.steady_gb = None
        self.cases_since_load = 0
        log_memory("AFTER QWEN RECYCLE")

    def step_count(self, steps):
        return TURBO_STEPS if self.turbo else steps

    def render(self, *, prompt, image, width, height, steps, seed, out_path, reporter, tag):
        err = None
        for attempt in (1, 2):
            err, oom = None, False
            reporter.start(self.step_count(steps))
            kwargs = result = img = g = None
            try:
                kwargs = {"prompt": prompt, "image": image, "height": height, "width": width}
                kwargs.update(qwen_sampling_kwargs(self.turbo, steps))
                g = make_generator(seed)
                if g is not None:
                    kwargs["generator"] = g
                if supports_step_callback(self.pipe):
                    kwargs["callback_on_step_end"] = reporter
                log_memory(f"{tag} BEFORE (attempt {attempt})")
                result = self.pipe(**kwargs)
                if result is None or not result.images:
                    raise RuntimeError("Pipeline returned no image.")
                img = result.images[0]
                img.save(out_path, format="PNG")
            except GenerationCancelled:
                err = "cancelled"
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                oom = is_memory_error(exc)
                print(f"[{tag}] FAILED (attempt {attempt}): {err}", flush=True)
                traceback.print_exc()
            kwargs = result = img = g = None
            if err is not None:
                fn = getattr(self.pipe, "maybe_free_model_hooks", None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
            free_memory(deep=True)
            if err is None:
                return None
            if err == "cancelled" or not oom or attempt == 2:
                return err
            self.recycle("lỗi hết bộ nhớ ở " + tag)
        return err

    def after_case(self, tag, more):
        free_memory(deep=True)
        log_memory(f"AFTER {tag}")
        self.cases_since_load += 1
        used = gpu_used_gb()
        if used is not None and self.steady_gb is None:
            self.steady_gb = used
        if not more:
            return
        if RECYCLE_EVERY and self.cases_since_load >= RECYCLE_EVERY:
            self.recycle(f"định kỳ mỗi {RECYCLE_EVERY} case")
            return
        if used is not None and self.steady_gb is not None and RECYCLE_GROWTH_GB > 0:
            delta = used - self.steady_gb
            if delta > RECYCLE_GROWTH_GB:
                self.recycle(f"bộ nhớ GPU tăng +{delta:.1f} GB so với baseline")



# ============================================================
# SINGLE GENERATION
# ============================================================
def _generate_blocking(mode, prompt, refs, width, height, steps, seed, cfg, negative_prompt):
    error, payload = None, None
    try:
        progress.update({"running": True, "step": 0, "total": 0, "percent": 0, "message": "Loading model"})
        kwargs = image = g = None
        try:
            if mode in ("edit", "transparent"):
                pipe = get_qwen_pipeline()
                set_qwen_mode(pipe, False)
                n_steps = steps
                final_prompt = build_rgba_prompt(prompt) if mode == "transparent" else prompt
                kwargs = {"prompt": final_prompt, "height": height, "width": width, "num_inference_steps": steps}
                if refs:
                    kwargs["image"] = refs[0] if len(refs) == 1 else refs
                    if mode == "transparent":
                        print(
                            f"[QWEN][TRANSPARENT] input reference = "
                            f"{len(refs)} image(s), "
                            f"mode={refs[0].mode if refs else None}, "
                            f"size={refs[0].size if refs else None}; "
                            f"source=prepared_reference",
                            flush=True,
                        )
                if cfg:
                    kwargs["true_cfg_scale"] = 4.0
                    if negative_prompt.strip():
                        kwargs["negative_prompt"] = negative_prompt.strip()
            else:
                pipe = get_zimage_pipeline()
                n_steps = 9
                kwargs = {"prompt": prompt, "height": height, "width": width,
                          "num_inference_steps": 9, "guidance_scale": 0.0}
            g = make_generator(seed)
            if g is not None:
                kwargs["generator"] = g
            if supports_step_callback(pipe):
                kwargs["callback_on_step_end"] = make_simple_progress(n_steps, "Generating")
            image = pipe(**kwargs).images[0]
            warning = None
            if mode == "transparent":
                image, warning = postprocess_transparent_output(image)

            filename = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
            image.save(OUTPUT_DIR / filename, format="PNG")
            payload = {"success": True, "filename": filename, "url": f"/images/{filename}",
                       "width": image.width, "height": image.height, "seed": seed,
                       "mode": mode, "input_reference_processed": bool(mode == "transparent" and refs)}
            if warning:
                payload["warning"] = warning
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        kwargs = image = g = None
        free_memory(deep=True)
        progress.update({"running": False, "percent": 100 if error is None else progress["percent"],
                         "message": "Done" if error is None else "Failed: " + error})
    finally:
        pass
    if error is not None:
        return {"success": False, "error": error, "status_code": 500}
    return payload

# ============================================================
# 8-CASE WORKER
# ============================================================
def run_8_cases(job_id, product_images, aspect_ratio, resolution, steps, seed,
                description, remove_bg, mode, prompt_enhance=True, aspect_auto=False):
    case_dir = BATCH_DIR / job_id
    cancel = cancel_events.get(job_id) or threading.Event()
    started = time.time()
    results = []
    session = None
    try:
        case_dir.mkdir(parents=True, exist_ok=True)
        n_refs = len(product_images)
        update_batch(job_id, status="running",
                     message=f"Preparing {n_refs} reference image(s)" if n_refs > 1 else "Preparing reference image",
                     persist=True)

        if cancel.is_set():
            raise GenerationCancelled()
        refs, ref_notes = prepare_reference_set(product_images, remove_bg)
        for idx, prepared in enumerate(refs):
            if cancel.is_set():
                raise GenerationCancelled()
            prepared.save(case_dir / reference_filename(idx), format="PNG")
            update_batch(
                job_id,
                reference_urls=[f"/batch-image/{job_id}/{reference_filename(k)}" for k in range(idx + 1)],
                reference_url=f"/batch-image/{job_id}/{REF_NAME_PRIMARY}",
                message=f"Prepared reference {idx + 1}/{n_refs}: {ref_notes[idx]}",
            )
        ref_note = ref_notes[0] if ref_notes else ""
        product_image = refs[0] if len(refs) == 1 else refs

        palette = product_palette(refs) if COLOR_LOCK_ENABLED else []
        color_note = make_color_note(refs, palette)
        palette_hint = palette_sentence(palette) if palette else ""
        print("[COLOR] palette:", palette_hint or "(không đo được hoặc COLOR_LOCK tắt)", flush=True)
        with batch_jobs_lock:
            p = dict(batch_jobs[job_id].get("params") or {})
            p["color_note"] = color_note
            p["ref_count"] = n_refs
        update_batch(job_id, params=p, persist=True)
        raw_cases = build_case_prompts(description, color_note, palette_hint, n_refs)
        if prompt_enhance and PROMPT_ENHANCER_ENABLED:
            update_batch(job_id, status="enhancing_prompts", percent=0,
                         message="Loading Prompt Enhancer", persist=True)
            try:
                cases = enhance_case_prompts(raw_cases, product_image, job_id=job_id, cancel=cancel,
                                         aspect_hint=None if aspect_auto else aspect_ratio,
                                         color_note=color_note)
            except GenerationCancelled:
                update_batch(job_id, status="cancelled", percent=100,
                             message="Đã huỷ Prompt Enhancer. Không load Qwen.", persist=True)
                return
            save_prompt_manifest(case_dir, cases)
            if cancel.is_set():
                update_batch(job_id, status="cancelled", percent=100,
                             message="Đã huỷ Prompt Enhancer. Không load Qwen.", persist=True)
                return
            unload_prompt_enhancer()
            free_memory(deep=True)
        else:
            cases = [{
                "slug": c["slug"], "name": c["name"], "prompt": c["fallback"],
                "original_prompt": c["intent"], "enhanced_prompt": c["fallback"],
                "fallback_prompt": c["fallback"],
                "prompt_enhancer": {"parse_ok": False, "disabled": True},
            } for c in raw_cases]
            save_prompt_manifest(case_dir, cases)

        total = len(cases)
        if cancel.is_set():
            update_batch(job_id, status="cancelled", percent=100,
                         message="Đã huỷ trước khi load Qwen.", persist=True)
            return
        update_batch(job_id, status="running", percent=20 if prompt_enhance else 0,
                     message="Loading Qwen-Image-2.1", persist=True)
        session = QwenSession(
            draft=(mode == "draft"),
            notify=lambda m: update_batch(job_id, message=m, warning=m),
        )
        if mode == "draft" and not session.turbo:
            steps = min(steps, 20)
            update_batch(job_id, warning=f"Turbo không khả dụng ({qwen_state['turbo_error']}); chạy base {steps} bước")

        swap0 = swap_used_gb()
        log_memory("BATCH START")

        for i, case in enumerate(cases):
            if cancel.is_set():
                break
            slug, name, prompt = case["slug"], case["name"], case["prompt"]
            case_seed = seed + i
            width, height = get_dimensions(
                pick_case_ratio(aspect_ratio, aspect_auto, case.get("prompt_enhancer")), resolution)
            label = "draft" if session.turbo else "final"
            tag = f"CASE {i+1}/{total}"
            title = f"Case {i+1}/{total} ({label}): {name}"
            t0 = time.time()
            update_batch(job_id, case=i+1, total=total,
                         percent=max(20 if prompt_enhance else 0, int(20 + i / total * 80) if prompt_enhance else int(i / total * 100)),
                         message=title)
            filename = f"{i+1:02d}_{slug}.png"
            reporter = StepReporter(
                job_id, cancel, title, done_before=i, case_total=total,
                base_percent=20 if prompt_enhance else 0,
                span_percent=80 if prompt_enhance else 100,
            )
            err = session.render(
                prompt=prompt, image=product_image, width=width, height=height,
                steps=steps, seed=case_seed, out_path=case_dir / filename,
                reporter=reporter, tag=tag
            )
            secs = round(time.time() - t0, 2)
            if err is None:
                item = {"case": i+1, "name": name, "filename": filename,
                        "url": f"/batch-image/{job_id}/{filename}",
                        "seconds": secs, "seed": case_seed, "mode": label, "size": f"{width}x{height}",
                        "status": "completed"}
            elif err == "cancelled":
                item = {"case": i+1, "name": name, "status": "cancelled", "seconds": secs}
            else:
                item = {"case": i+1, "name": name, "status": "failed",
                        "error": err, "seconds": secs}
            results.append(item)

            if err != "cancelled":
                session.after_case(tag, more=(i < total - 1))
            swap_now = swap_used_gb()
            if swap0 is not None and swap_now is not None and swap_now - swap0 > SWAP_WARN_GB:
                update_batch(job_id, warning=f"Máy đang swap RAM (+{swap_now - swap0:.1f} GB) - nên dùng 1024/nháp hoặc APP_MPS_MODE=offload")

            done = sum(x["status"] == "completed" for x in results)
            failed = sum(x["status"] == "failed" for x in results)
            done_percent = int(len(results) / total * (80 if prompt_enhance else 100) + (20 if prompt_enhance else 0))
            update_batch(job_id, completed=done, failed=failed,
                         percent=done_percent,
                         message=f"Finished {len(results)}/{total}: {name}",
                         results=list(results), persist=True)

        total_seconds = round(time.time() - started, 2)
        done = sum(x["status"] == "completed" for x in results)
        if cancel.is_set():
            status, msg = "cancelled", f"Đã huỷ: {done}/{total} ảnh xong sau {total_seconds}s"
        elif done == 0:
            status, msg = "failed", "Cả 8 case đều lỗi - xem chi tiết từng case và log terminal"
        else:
            status, msg = "completed", f"Completed ({'draft' if session.turbo else 'final'}): {done}/{total} in {total_seconds}s"
        update_batch(job_id, status=status, percent=100, message=msg,
                     results=list(results), seconds=total_seconds, persist=True)
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        try:
            update_batch(job_id, status="failed", percent=100, message=error_text,
                         results=list(results), persist=True)
        except Exception:
            pass
    finally:
        unload_prompt_enhancer()
        session = None
        free_memory(deep=True)
        log_memory(f"BATCH {job_id} END")
        progress.update({"running": False, "percent": 100, "message": "Batch finished"})
        cancel_events.pop(job_id, None)


def run_rerender(job_id, case_numbers, steps):
    cancel = cancel_events.get(job_id) or threading.Event()
    session = None
    try:
        job = get_job(job_id)
        params = dict(job["params"])
        results = [dict(x) for x in job["results"]]
        case_dir = BATCH_DIR / job_id
        started = time.time()
        update_batch(job_id, status="running", percent=0, warning=None, message="Loading base model", persist=True)
        loaded = load_references(case_dir)
        reference = loaded[0] if len(loaded) == 1 else loaded
        session = QwenSession(draft=False, notify=lambda m: update_batch(job_id, message=m, warning=m))
        manifest = load_prompt_manifest(case_dir)
        raw = build_case_prompts(params.get("description", ""), params.get("color_note", ""), "",
                                 params.get("ref_count", 1))
        fallback = {
            i + 1: {"slug": c["slug"], "name": c["name"], "prompt": c["fallback"],
                    "original_prompt": c["intent"], "enhanced_prompt": c["fallback"]}
            for i, c in enumerate(raw)
        }
        if manifest:
            by_case = {i + 1: c for i, c in enumerate(manifest)}
            cases = [by_case.get(n) or fallback[n] for n in case_numbers]
        else:
            cases = [fallback[n] for n in case_numbers]
        n_total = len(case_numbers)
        errors = []
        for k, n in enumerate(case_numbers):
            if cancel.is_set():
                break
            case_data = cases[k]
            slug, name, prompt = case_data["slug"], case_data["name"], case_data["prompt"]
            width, height = get_dimensions(
                pick_case_ratio(params["aspect_ratio"], params.get("aspect_auto", False),
                                case_data.get("prompt_enhancer")), params["resolution"])
            tag = f"FINAL {k+1}/{n_total}"
            title = f"Final {k+1}/{n_total} ({steps} bước): {name}"
            t0 = time.time()
            filename = f"{n:02d}_{slug}_final.png"
            reporter = StepReporter(job_id, cancel, title, done_before=k, case_total=n_total)
            err = session.render(prompt=prompt, image=reference, width=width, height=height, steps=steps,
                                 seed=params["seed"] + n - 1, out_path=case_dir / filename, reporter=reporter, tag=tag)
            if err is None:
                for r in results:
                    if r.get("case") == n:
                        r["final_url"] = f"/batch-image/{job_id}/{filename}"
                        r["final_seconds"] = round(time.time() - t0, 2)
            elif err != "cancelled":
                errors.append(f"Case {n}: {err}")
            if err != "cancelled":
                session.after_case(tag, more=(k < n_total - 1))
            update_batch(job_id, results=[dict(x) for x in results],
                         warning="; ".join(errors) or None, persist=True)
        total_seconds = round(time.time() - started, 2)
        if cancel.is_set():
            update_batch(job_id, status="cancelled", percent=100, message="Đã huỷ render bản cuối",
                         results=[dict(x) for x in results], persist=True)
        else:
            update_batch(job_id, status="completed", percent=100,
                         message=f"Final re-render done in {total_seconds}s", results=[dict(x) for x in results],
                         persist=True)
    except Exception as exc:
        error_text = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        try:
            update_batch(job_id, status="failed", percent=100, message="Final re-render failed: " + error_text, persist=True)
        except Exception:
            pass
    finally:
        session = None
        free_memory(deep=True)
        log_memory(f"RERENDER {job_id} END")
        progress.update({"running": False, "percent": 100, "message": "Final re-render finished"})
        cancel_events.pop(job_id, None)


# ============================================================
# GPU FIFO WORKER
# ============================================================
def gpu_worker_loop():
    """Single process-local GPU worker. Tasks are executed strictly FIFO."""
    global gpu_worker_thread
    print("[QUEUE] GPU FIFO worker started", flush=True)
    while True:
        task = gpu_queue.get()
        future = task.get("future")
        kind = task.get("kind")
        job_id = task.get("job_id")
        try:
            if job_id:
                job = get_job(job_id)
                cancel = cancel_events.get(job_id)
                if cancel is not None and cancel.is_set() and job and job.get("status") == "queued":
                    update_batch(job_id, status="cancelled", percent=100,
                                 message="Đã huỷ trước khi GPU worker bắt đầu.", persist=True)
                    continue

            if kind == "single":
                result = _generate_blocking(*task["args"])
                if future is not None and not future.done():
                    future.set_result(result)
            elif kind == "batch":
                run_8_cases(*task["args"])
            elif kind == "rerender":
                run_rerender(*task["args"])
            else:
                raise RuntimeError(f"Unknown GPU queue task: {kind}")
        except Exception as exc:
            traceback.print_exc()
            if job_id:
                update_batch(job_id, status="failed", percent=100,
                             message=f"Queue worker error: {type(exc).__name__}: {exc}", persist=True)
            if future is not None and not future.done():
                future.set_exception(exc)
        finally:
            gpu_queue.task_done()


def start_gpu_worker():
    global gpu_worker_thread
    with gpu_worker_start_lock:
        if gpu_worker_thread is not None and gpu_worker_thread.is_alive():
            return
        gpu_worker_thread = threading.Thread(
            target=gpu_worker_loop,
            name="gpu-fifo-worker",
            daemon=True,
        )
        gpu_worker_thread.start()


# ============================================================
# API CHO WEB LAYER (không phụ thuộc FastAPI)
# ============================================================
def prepare_single_references(mode, refs, remove_bg, prompt=""):
    """Chuẩn hoá ảnh tham chiếu cho /generate (Edit + Transparent) - cùng pipeline với 8-case."""
    refs = list(refs or [])
    # FIX: Image Edit + Transparent single-generation phải dùng cùng reference
    # preparation pipeline. Trước đây chỉ Transparent được prepare, còn Edit
    # truyền ảnh upload nguyên bản trực tiếp vào Qwen. Điều này làm workflow
    # "remove background / crop / padding" không nhất quán với batch workflow.
    if mode in ("edit", "transparent") and refs:
        raw_refs = refs
        prepared_refs = []
        target_aspect = raw_refs[0].width / max(1, raw_refs[0].height)
        for idx, raw_ref in enumerate(raw_refs):
            prepared, note = prepare_reference(
                raw_ref, remove_bg, target_aspect=target_aspect
            )
            prepared_refs.append(prepared)
            print(
                f"[REF][{mode.upper()}] reference {idx + 1}/{len(raw_refs)}: "
                f"raw={raw_ref.size}/{raw_ref.mode} -> "
                f"prepared={prepared.size}/{prepared.mode}; {note}",
                flush=True,
            )
        refs = prepared_refs

    # Diagnostic: ghi rõ những gì thực sự đi vào Qwen. Đặc biệt hữu ích khi
    # Prompt Enhancer trả về <image1>, vì token này chỉ có ý nghĩa khi Qwen
    # thực sự nhận condition image qua kwargs["image"].
    print("[QWEN][INPUT] mode=", mode, "refs=", len(refs), flush=True)
    for idx, ref in enumerate(refs, start=1):
        print(
            f"[QWEN][INPUT] image{idx}: size={getattr(ref, 'size', None)} "
            f"mode={getattr(ref, 'mode', None)}",
            flush=True,
        )
    print("[QWEN][INPUT] prompt=", prompt.strip(), flush=True)
    return refs


def queue_status():
    return {"depth": gpu_queue.qsize(),
            "worker_alive": bool(gpu_worker_thread and gpu_worker_thread.is_alive())}


def health_info():
    return {"status": "ok", "model": current_model, "device": DEVICE, "dtype": str(DTYPE),
            "running": progress["running"], "memory": get_memory_info(),
            "queue": queue_status(),
            "turbo": {"loaded": qwen_state["turbo_loaded"], "error": qwen_state["turbo_error"]},
            "prompt_enhancer": {"enabled": PROMPT_ENHANCER_ENABLED, "loaded": prompt_enhancer_model is not None}}
