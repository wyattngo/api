"""
runpod_handler.py — RunPod Serverless worker cho inference.py.

Không dùng FastAPI, không dùng gpu_queue: RunPod đã tự xếp hàng job, mỗi worker xử lý
1 job tại một thời điểm, nên handler gọi thẳng các hàm trong inference.py.

Chạy:
    python runpod_handler.py                          # worker thật (Docker CMD)
    python runpod_handler.py --test_input '{"input": {"action": "health"}}'   # test local

============================================================
REQUEST  (job["input"])
============================================================
Chung (mọi action trả ảnh):
    output        "auto" (mặc định) | "base64" | "s3"
                  auto = s3 nếu đã cấu hình BUCKET_*, ngược lại base64
    image_format  "png" (mặc định) | "webp" | "jpeg"
    images        list ảnh tham chiếu: base64 / data-URI / URL http(s)   (tối đa 10)

action = "generate" (mặc định)  — tương đương POST /generate
    prompt (bắt buộc), mode: "text" | "edit" | "transparent", aspect_ratio, resolution
    (1024/1536/2048), steps (20/30/40), seed (-1 = ngẫu nhiên), cfg, negative_prompt,
    remove_bg (mặc định true). mode "edit" bắt buộc có images.
    quality: "fast" (mặc định) | "fine".
      mode "text":        fast = Z-Image (9 bước cố định), fine = Qwen text-to-image (dùng steps/cfg)
      mode "transparent": fast = chỉ rembg (tách nền, giữ kích thước ảnh gốc, cần 1 ảnh trong images),
                          fine = Qwen tạo ảnh RGBA
      mode "edit":        luôn dùng Qwen

action = "batch"  — tương đương POST /generate-8-cases
    images (bắt buộc), aspect_ratio ("auto" mặc định), resolution, steps, seed, description,
    remove_bg (true), mode: "draft" (mặc định) | "final", prompt_enhance (true),
    return_references (false), return_prompts (true)

action = "rerender"  — tương đương POST /rerender-final
    RunPod worker không giữ trạng thái giữa các job (đĩa cục bộ mất khi worker tắt), nên
    client phải gửi lại đủ ngữ cảnh mà kết quả của "batch" đã trả về:
      images   ảnh GỐC (worker tự tách nền lại theo params.remove_bg)
      cases    [1, 3, 8]  (1..8)
      steps    20/30/40
      params   object "params" nhận từ kết quả batch
      prompts  list "prompts" nhận từ kết quả batch (không gửi => dùng prompt fallback)

action = "health"
    trả device / dtype / model đang nạp / bộ nhớ GPU

============================================================
RESPONSE
============================================================
Thành công: dict JSON. Mỗi ảnh có dạng
    {"format": "png", "mime": "image/png", "bytes": N, "base64": "..."}     (output=base64)
    {"format": "png", "mime": "image/png", "bytes": N, "url": "https://..."} (output=s3)
Thất bại: {"error": "..."} (RunPod đánh dấu job FAILED).

GIỚI HẠN QUAN TRỌNG: RunPod giới hạn payload 10 MB cho /run và 20 MB cho /runsync (cả
chiều trả về). Batch 8 ảnh gần như chắc chắn vượt mức này nếu trả base64, nên với batch
hãy cấu hình S3 (BUCKET_ENDPOINT_URL, BUCKET_ACCESS_KEY_ID, BUCKET_SECRET_ACCESS_KEY).
Nếu không có S3 và tổng base64 vượt RUNPOD_MAX_INLINE_MB, handler tự đổi PNG -> WebP;
vẫn quá lớn thì trả lỗi rõ ràng thay vì để gateway làm rơi kết quả.

============================================================
BIẾN MÔI TRƯỜNG (ngoài các APP_* của inference.py)
============================================================
    RUNPOD_PRELOAD         qwen (mặc định) | zimage | none — nạp model lúc worker khởi động
    RUNPOD_MAX_INLINE_MB   9      trần tổng base64 trong 1 response
    RUNPOD_ALLOW_URL_INPUT 1      cho phép images là URL http(s) (chặn IP nội bộ)
    RUNPOD_MAX_DOWNLOAD_MB 40     trần dung lượng mỗi ảnh tải từ URL
    RUNPOD_PROGRESS_SECS   2      chu kỳ gửi progress_update
    RUNPOD_KEEP_FILES      0      1 = không xoá file trung gian trên đĩa worker
Gợi ý cho GPU lớn (A100/H100 80GB): APP_CPU_OFFLOAD=0 để giữ model trên GPU.
"""
import base64
import io
import ipaddress
import json
import os
import random
import re
import shutil
import socket
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# QUAN TRỌNG: import inference TRƯỚC mọi thứ có thể kéo torch vào; inference.py đặt
# PYTORCH_ENABLE_MPS_FALLBACK / watermark trước `import torch`.
import inference as I

from PIL import Image, ImageOps

import runpod

try:
    from runpod.serverless.utils import rp_upload
except Exception:  # SDK cũ / thiếu boto3
    rp_upload = None


# ============================================================
# CONFIG
# ============================================================
def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


VALID_MODES = {"text", "edit", "transparent"}
VALID_RESOLUTIONS = {1024, 1536, 2048}
VALID_STEPS = {20, 30, 40}
VALID_FORMATS = {"png", "webp", "jpeg"}
VALID_OUTPUTS = {"auto", "base64", "s3"}

MAX_INLINE_BYTES = int(_env_float("RUNPOD_MAX_INLINE_MB", 9) * 1024 * 1024)
MAX_DOWNLOAD_BYTES = int(_env_float("RUNPOD_MAX_DOWNLOAD_MB", 40) * 1024 * 1024)
DOWNLOAD_TIMEOUT = 30
PROGRESS_SECONDS = _env_float("RUNPOD_PROGRESS_SECS", 2)
ALLOW_URL_INPUT = os.environ.get("RUNPOD_ALLOW_URL_INPUT", "1") == "1"
KEEP_FILES = os.environ.get("RUNPOD_KEEP_FILES", "0") == "1"
SLUG_RE = re.compile(r"^[A-Za-z0-9_\-]{1,48}$")


class BadInput(ValueError):
    """Lỗi do client gửi sai input."""


class OutputTooLarge(RuntimeError):
    """Kết quả vượt giới hạn payload của RunPod."""


# ============================================================
# INPUT PARSING
# ============================================================
def as_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value, default, name="value"):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise BadInput(f"'{name}' phải là số nguyên")


def pick(value, allowed, default):
    """Giá trị ngoài danh sách cho phép -> mặc định (giống các endpoint gốc)."""
    return value if value in allowed else default


def _check_public_host(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BadInput("URL ảnh không hợp lệ")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BadInput(f"Không phân giải được host: {parsed.hostname}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise BadInput("URL trỏ tới địa chỉ nội bộ, bị chặn")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _download(url):
    """Tải ảnh; tự theo redirect nhưng kiểm tra lại host ở từng bước."""
    opener = urllib.request.build_opener(_NoRedirect)
    for _ in range(4):
        _check_public_host(url)
        request = urllib.request.Request(url, headers={"User-Agent": "runpod-worker/1.0"})
        host = urllib.parse.urlparse(url).netloc
        try:
            response = opener.open(request, timeout=DOWNLOAD_TIMEOUT)
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") if exc.headers else None
            if exc.code in (301, 302, 303, 307, 308) and location:
                url = urllib.parse.urljoin(url, location)
                continue
            raise BadInput(f"Không tải được ảnh từ {host} (HTTP {exc.code})")
        except Exception as exc:
            raise BadInput(f"Không tải được ảnh từ {host}: {type(exc).__name__}")
        with response:
            data = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise BadInput(f"Ảnh từ {host} lớn hơn {MAX_DOWNLOAD_BYTES // 1024 // 1024} MB")
        return data
    raise BadInput("Quá nhiều lần chuyển hướng khi tải ảnh")


def decode_image(spec, label):
    if not isinstance(spec, str) or not spec.strip():
        raise BadInput(f"{label}: cần chuỗi base64, data-URI hoặc URL")
    text = spec.strip()
    if text.startswith(("http://", "https://")):
        if not ALLOW_URL_INPUT:
            raise BadInput("Worker này không cho phép ảnh dạng URL")
        data = _download(text)
    else:
        if text.startswith("data:"):
            comma = text.find(",")
            if comma < 0:
                raise BadInput(f"{label}: data-URI không hợp lệ")
            text = text[comma + 1:]
        text = "".join(text.split())
        text += "=" * (-len(text) % 4)
        try:
            data = base64.b64decode(text)
        except Exception:
            raise BadInput(f"{label}: base64 không hợp lệ")
    if not data:
        raise BadInput(f"{label}: ảnh rỗng")
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except Exception as exc:
        raise BadInput(f"{label}: không đọc được ảnh ({type(exc).__name__})")
    if I.MAX_INPUT_SIDE > 0 and max(image.size) > I.MAX_INPUT_SIDE:
        image.thumbnail((I.MAX_INPUT_SIDE, I.MAX_INPUT_SIDE), Image.LANCZOS)
    return image


def load_images(inp):
    raw = inp.get("images", inp.get("image"))
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise BadInput("'images' phải là list")
    if len(raw) > I.MAX_REFERENCE_IMAGES:
        raise BadInput(f"Tối đa {I.MAX_REFERENCE_IMAGES} ảnh tham chiếu (bạn gửi {len(raw)})")
    return [decode_image(item, f"images[{i}]") for i, item in enumerate(raw)]


def output_options(inp):
    output = str(inp.get("output", "auto")).strip().lower()
    fmt = str(inp.get("image_format", "png")).strip().lower()
    if fmt == "jpg":
        fmt = "jpeg"
    if output not in VALID_OUTPUTS:
        raise BadInput(f"'output' phải thuộc {sorted(VALID_OUTPUTS)}")
    if fmt not in VALID_FORMATS:
        raise BadInput(f"'image_format' phải thuộc {sorted(VALID_FORMATS)}")
    return output, fmt


# ============================================================
# OUTPUT: base64 / S3
# ============================================================
def bucket_configured():
    return bool(rp_upload) and all(
        os.environ.get(k) for k in ("BUCKET_ENDPOINT_URL", "BUCKET_ACCESS_KEY_ID", "BUCKET_SECRET_ACCESS_KEY"))


def _encode(path, fmt):
    """-> (bytes, mime, ext)"""
    if fmt == "png":
        return Path(path).read_bytes(), "image/png", "png"
    with Image.open(path) as src:
        image = src.copy()
    buf = io.BytesIO()
    if fmt == "jpeg":
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            image = flat
        else:
            image = image.convert("RGB")
        image.save(buf, format="JPEG", quality=92)
        return buf.getvalue(), "image/jpeg", "jpeg"
    image.save(buf, format="WEBP", quality=90)
    return buf.getvalue(), "image/webp", "webp"


def package_images(job_id, files, fmt, output):
    """files: list[Path] -> (list[dict], list[str warnings])"""
    warnings = []
    use_s3 = output == "s3" or (output == "auto" and bucket_configured())
    if output == "s3" and not bucket_configured():
        raise BadInput("output=s3 nhưng worker chưa cấu hình BUCKET_ENDPOINT_URL / "
                       "BUCKET_ACCESS_KEY_ID / BUCKET_SECRET_ACCESS_KEY")

    if use_s3:
        packaged = []
        for path in files:
            path = Path(path)
            if fmt == "png":
                upload_path, mime = path, "image/png"
            else:
                data, mime, ext = _encode(path, fmt)
                upload_path = path.with_suffix("." + ext)
                upload_path.write_bytes(data)
            url = rp_upload.upload_image(job_id, str(upload_path))
            packaged.append({"format": fmt, "mime": mime,
                             "bytes": upload_path.stat().st_size, "url": url})
        return packaged, warnings

    def build(target_fmt):
        items = []
        for path in files:
            data, mime, ext = _encode(path, target_fmt)
            items.append({"format": ext, "mime": mime, "bytes": len(data),
                          "base64": base64.b64encode(data).decode("ascii")})
        return items

    items = build(fmt)
    total = sum(len(i["base64"]) for i in items)
    if total > MAX_INLINE_BYTES and fmt == "png":
        items = build("webp")
        total = sum(len(i["base64"]) for i in items)
        warnings.append("Output PNG vượt giới hạn payload nên đã tự đổi sang WebP (quality 90). "
                        "Cấu hình BUCKET_* để nhận PNG gốc qua URL.")
    if total > MAX_INLINE_BYTES:
        raise OutputTooLarge(
            f"Kết quả {total / 1024 / 1024:.1f} MB (base64) vượt giới hạn "
            f"{MAX_INLINE_BYTES / 1024 / 1024:.0f} MB của RunPod. Cấu hình S3 (BUCKET_*) "
            f"và dùng output=s3, hoặc giảm resolution / image_format=jpeg.")
    return items, warnings


# ============================================================
# PROGRESS
# ============================================================
class ProgressPump:
    """Thread nền đọc trạng thái từ inference.py và gửi runpod.serverless.progress_update."""

    def __init__(self, job, getter):
        self.job, self.getter = job, getter
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="progress-pump", daemon=True)

    def _run(self):
        last = None
        while not self._stop.wait(PROGRESS_SECONDS):
            try:
                text = self.getter()
            except Exception:
                continue
            if text and text != last:
                last = text
                try:
                    runpod.serverless.progress_update(self.job, text)
                except Exception:
                    pass

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=3)


def _single_progress():
    p = dict(I.progress)
    return f"{p.get('percent', 0)}% · {p.get('message', '')}"


def _job_progress(job_id):
    def getter():
        j = I.get_job(job_id) or {}
        return f"{j.get('status', '')} {j.get('percent', 0)}% · {j.get('message', '')}"
    return getter


# ============================================================
# JOB HELPERS
# ============================================================
def looks_like_oom(text):
    text = (text or "").lower()
    return any(k in text for k in ("out of memory", "insufficient memory", "invalid buffer size",
                                   "mps backend out"))


def new_job(job_id, params, results=None):
    """Tạo job trong bộ nhớ giống endpoint gốc để run_8_cases / run_rerender dùng lại."""
    with I.batch_jobs_lock:
        I.batch_jobs[job_id] = {
            "job_id": job_id, "status": "queued", "case": 0, "total": 8, "completed": 0,
            "failed": 0, "percent": 0, "step": 0, "steps_total": 0, "message": "Queued",
            "results": list(results or []), "reference_url": None, "reference_urls": [],
            "warning": None, "params": params, "last_tick": time.time()}
        I._persist_job(job_id, I.batch_jobs[job_id])
    I.cancel_events[job_id] = threading.Event()


def forget_job(job_id):
    I.cancel_events.pop(job_id, None)
    with I.batch_jobs_lock:
        I.batch_jobs.pop(job_id, None)
    if not KEEP_FILES:
        shutil.rmtree(I.BATCH_DIR / job_id, ignore_errors=True)


def new_job_id():
    return uuid.uuid4().hex[:12]     # phải khớp JOB_ID_RE của inference.py


def parse_case_numbers(value):
    if isinstance(value, str):
        value = [x for x in value.split(",") if x.strip()]
    if not isinstance(value, list) or not value:
        raise BadInput("'cases' phải là list số 1..8, ví dụ [1, 3, 8]")
    try:
        numbers = sorted({int(x) for x in value})
    except (TypeError, ValueError):
        raise BadInput("'cases' phải là list số nguyên")
    numbers = [n for n in numbers if 1 <= n <= 8]
    if not numbers:
        raise BadInput("Không có case hợp lệ (1..8)")
    return numbers


# ============================================================
# ACTIONS
# ============================================================
def _cutout_fast(refs, job, fmt, output):
    """quality=fast cho mode transparent: tách nền bằng rembg, không nạp model khuếch tán."""
    if not refs:
        raise BadInput("Xóa nền cần một ảnh trong 'images'.")
    try:
        cut, warning = I.cutout_fast(refs[0])
    except RuntimeError as exc:
        return {"error": str(exc)}
    out_file = I.OUTPUT_DIR / f"{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
    I.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        cut.save(out_file, format="PNG")
        images, warnings = package_images(job.get("id") or new_job_id(), [out_file], fmt, output)
        response = {"success": True, "mode": "transparent", "quality": "fast", "seed": 0,
                    "width": cut.width, "height": cut.height, "image": images[0]}
        if warning:
            warnings.append(warning)
        if warnings:
            response["warnings"] = warnings
        return response
    finally:
        if not KEEP_FILES:
            for f in out_file.parent.glob(out_file.stem + ".*"):
                f.unlink(missing_ok=True)


def action_generate(inp, job):
    prompt = str(inp.get("prompt") or "").strip()
    if not prompt:
        raise BadInput("Prompt cannot be empty.")
    mode = str(inp.get("mode", "text")).strip().lower()
    if mode not in VALID_MODES:
        raise BadInput(f"'mode' phải thuộc {sorted(VALID_MODES)}")
    output, fmt = output_options(inp)

    aspect_ratio = str(inp.get("aspect_ratio", "1:1"))
    resolution = pick(as_int(inp.get("resolution"), 1024, "resolution"), VALID_RESOLUTIONS, 1024)
    steps = pick(as_int(inp.get("steps"), 40, "steps"), VALID_STEPS, 40)
    seed = as_int(inp.get("seed"), -1, "seed")
    if seed < 0:
        seed = random.SystemRandom().randint(0, 2**31 - 16)   # trả seed thật để tái lập được
    cfg = as_bool(inp.get("cfg"), False)
    negative_prompt = str(inp.get("negative_prompt") or "")
    remove_bg = as_bool(inp.get("remove_bg"), True)
    quality = pick(str(inp.get("quality") or "fast").strip().lower(), {"fast", "fine"}, "fast")

    refs = load_images(inp)
    if mode == "edit" and not refs:
        raise BadInput("Image Edit mode requires a reference image.")
    if mode == "transparent" and quality == "fast":
        return _cutout_fast(refs, job, fmt, output)

    # Cùng pipeline chuẩn bị ảnh (rembg / crop / pad) với endpoint /generate.
    refs = I.prepare_single_references(mode, refs, remove_bg, prompt)

    ratio_key = I.resolve_aspect(aspect_ratio, refs[0] if refs else None)
    if mode in ("edit", "transparent"):
        width, height = I.get_dimensions(ratio_key, resolution)
    else:
        width, height = I.get_dimensions_long_side(ratio_key, resolution)

    out_file = None
    try:
        with ProgressPump(job, _single_progress):
            result = I._generate_blocking(mode, prompt, refs, width, height, steps, seed, cfg,
                                          negative_prompt, quality=quality)
        if not result.get("success"):
            err = result.get("error", "Unknown inference error")
            return {"error": err, **({"refresh_worker": True} if looks_like_oom(err) else {})}

        out_file = I.OUTPUT_DIR / result["filename"]
        images, warnings = package_images(job.get("id") or new_job_id(), [out_file], fmt, output)
        response = {"success": True, "mode": mode, "quality": quality, "seed": seed,
                    "width": result["width"], "height": result["height"],
                    "input_reference_processed": result.get("input_reference_processed", False),
                    "image": images[0]}
        if result.get("warning"):
            warnings.append(result["warning"])
        if warnings:
            response["warnings"] = warnings
        return response
    finally:
        if out_file is not None and not KEEP_FILES:
            for f in out_file.parent.glob(out_file.stem + ".*"):
                f.unlink(missing_ok=True)


def action_batch(inp, job):
    images = load_images(inp)
    if not images:
        raise BadInput("Chưa có ảnh sản phẩm ('images').")
    output, fmt = output_options(inp)

    aspect_in = str(inp.get("aspect_ratio", "auto"))
    resolution = pick(as_int(inp.get("resolution"), 1024, "resolution"), VALID_RESOLUTIONS, 1024)
    steps = pick(as_int(inp.get("steps"), 40, "steps"), VALID_STEPS, 40)
    seed = as_int(inp.get("seed"), -1, "seed")
    if seed < 0:
        seed = random.SystemRandom().randint(0, 2**31 - 16)
    description = str(inp.get("description") or "")
    remove_bg = as_bool(inp.get("remove_bg"), True)
    mode = pick(str(inp.get("mode", "draft")).strip().lower(), {"draft", "final"}, "draft")
    prompt_enhance = as_bool(inp.get("prompt_enhance"), True)

    aspect_auto = aspect_in == "auto"
    aspect_ratio = I.resolve_aspect(aspect_in, images[0])

    job_id = new_job_id()
    params = {"aspect_ratio": aspect_ratio, "aspect_auto": aspect_auto, "resolution": resolution,
              "steps": steps, "seed": seed, "description": description, "remove_bg": remove_bg,
              "mode": mode, "prompt_enhance": prompt_enhance, "ref_count": len(images)}
    new_job(job_id, params)
    case_dir = I.BATCH_DIR / job_id

    try:
        with ProgressPump(job, _job_progress(job_id)):
            I.run_8_cases(job_id, images, aspect_ratio, resolution, steps, seed, description,
                          remove_bg, mode, prompt_enhance, aspect_auto)

        j = I.get_job(job_id) or {}
        status = j.get("status")
        if status != "completed":
            msg = j.get("message") or f"Batch kết thúc với trạng thái '{status}'"
            return {"error": msg, **({"refresh_worker": True} if looks_like_oom(msg) else {})}

        results = [dict(r) for r in j.get("results", [])]
        done = [r for r in results if r.get("status") == "completed"]
        files = [case_dir / r["filename"] for r in done]
        packaged, warnings = package_images(job_id, files, fmt, output)
        for r, pkg in zip(done, packaged):
            r["image"] = pkg
            r.pop("url", None)          # đường dẫn /batch-image/... chỉ có nghĩa với app.py
        if j.get("warning"):
            warnings.append(j["warning"])

        final_params = dict(j.get("params") or params)
        response = {"success": True, "job_id": job_id, "status": status, "seed": seed,
                    "completed": j.get("completed", len(done)), "failed": j.get("failed", 0),
                    "seconds": j.get("seconds"), "message": j.get("message"),
                    "params": final_params, "results": results}
        if as_bool(inp.get("return_prompts"), True):
            response["prompts"] = I.load_prompt_manifest(case_dir) or []
        if as_bool(inp.get("return_references"), False):
            ref_files = [case_dir / I.reference_filename(k) for k in range(len(images))]
            ref_files = [f for f in ref_files if f.is_file()]
            refs_pkg, ref_warn = package_images(job_id, ref_files, fmt, output)
            response["references"] = refs_pkg
            warnings += ref_warn
        if warnings:
            response["warnings"] = warnings
        return response
    finally:
        forget_job(job_id)


def action_rerender(inp, job):
    images = load_images(inp)
    if not images:
        raise BadInput("Rerender cần gửi lại ảnh gốc trong 'images' (worker không giữ trạng thái).")
    output, fmt = output_options(inp)
    numbers = parse_case_numbers(inp.get("cases"))
    steps = pick(as_int(inp.get("steps"), 40, "steps"), VALID_STEPS, 40)

    p_in = inp.get("params")
    if not isinstance(p_in, dict):
        raise BadInput("Thiếu 'params' (object 'params' nhận từ kết quả batch).")
    seed = as_int(p_in.get("seed"), None, "params.seed")
    if seed is None or seed < 0:
        raise BadInput("'params.seed' bắt buộc (seed nhận từ kết quả batch).")
    aspect_ratio = str(p_in.get("aspect_ratio", "1:1"))
    if aspect_ratio not in I.ASPECT_RATIOS:
        raise BadInput(f"'params.aspect_ratio' phải thuộc {sorted(I.ASPECT_RATIOS)}")
    params = {
        "aspect_ratio": aspect_ratio,
        "aspect_auto": as_bool(p_in.get("aspect_auto"), False),
        "resolution": pick(as_int(p_in.get("resolution"), 1024, "params.resolution"), VALID_RESOLUTIONS, 1024),
        "steps": steps, "seed": seed,
        "description": str(p_in.get("description") or ""),
        "color_note": str(p_in.get("color_note") or ""),
        "remove_bg": as_bool(p_in.get("remove_bg"), True),
        "ref_count": len(images),
    }

    prompts = inp.get("prompts")
    manifest = None
    if prompts:
        if not isinstance(prompts, list):
            raise BadInput("'prompts' phải là list nhận từ kết quả batch")
        for k, item in enumerate(prompts):
            if not isinstance(item, dict):
                raise BadInput(f"prompts[{k}] phải là object")
            slug = str(item.get("slug", ""))
            if not SLUG_RE.match(slug):       # slug đi vào tên file -> chặn path traversal
                raise BadInput(f"prompts[{k}].slug không hợp lệ")
            if not (item.get("prompt") or item.get("enhanced_prompt")):
                raise BadInput(f"prompts[{k}] thiếu 'prompt' / 'enhanced_prompt'")
            item.setdefault("name", slug)
        manifest = prompts

    job_id = new_job_id()
    case_dir = I.BATCH_DIR / job_id
    new_job(job_id, params, results=[{"case": n} for n in numbers])
    try:
        case_dir.mkdir(parents=True, exist_ok=True)
        refs, _notes = I.prepare_reference_set(images, params["remove_bg"])
        for idx, prepared in enumerate(refs):
            prepared.save(case_dir / I.reference_filename(idx), format="PNG")
        if manifest:
            (case_dir / "prompts.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        with ProgressPump(job, _job_progress(job_id)):
            I.run_rerender(job_id, numbers, steps)

        j = I.get_job(job_id) or {}
        finals = []
        for n in numbers:
            found = sorted(case_dir.glob(f"{n:02d}_*_final.png"))
            if found:
                finals.append((n, found[0]))
        if j.get("status") != "completed" or not finals:
            msg = j.get("warning") or j.get("message") or "Rerender không tạo được ảnh nào"
            return {"error": msg, **({"refresh_worker": True} if looks_like_oom(msg) else {})}

        packaged, warnings = package_images(job_id, [f for _, f in finals], fmt, output)
        response = {
            "success": True, "job_id": job_id, "status": j.get("status"), "steps": steps,
            "cases": [{"case": n, "filename": f.name, "image": pkg}
                      for (n, f), pkg in zip(finals, packaged)],
        }
        if not manifest:
            warnings.append("Không gửi 'prompts' nên dùng prompt fallback, không phải prompt đã qua "
                            "Prompt Enhancer của batch gốc.")
        if j.get("warning"):
            warnings.append(j["warning"])
        if warnings:
            response["warnings"] = warnings
        return response
    finally:
        forget_job(job_id)


def action_health(inp, job):
    info = I.health_info()
    info.pop("queue", None)          # RunPod tự quản lý hàng đợi
    info["output"] = {"bucket_configured": bucket_configured(),
                      "max_inline_mb": round(MAX_INLINE_BYTES / 1024 / 1024, 1)}
    return info


ACTIONS = {
    "generate": action_generate,
    "batch": action_batch,
    "rerender": action_rerender,
    "health": action_health,
}


# ============================================================
# HANDLER
# ============================================================
def handler(job):
    inp = job.get("input") or {}
    if not isinstance(inp, dict):
        return {"error": "'input' phải là object JSON"}
    action = str(inp.get("action", "generate")).strip().lower()
    started = time.time()
    try:
        fn = ACTIONS.get(action)
        if fn is None:
            raise BadInput(f"action không hợp lệ '{action}'. Hỗ trợ: {sorted(ACTIONS)}")
        out = fn(inp, job)
        if isinstance(out, dict) and "error" not in out:
            out.setdefault("handler_seconds", round(time.time() - started, 2))
        return out
    except BadInput as exc:
        return {"error": f"Invalid input: {exc}"}
    except OutputTooLarge as exc:
        return {"error": str(exc)}
    except Exception as exc:
        traceback.print_exc()
        message = f"{type(exc).__name__}: {exc}"
        result = {"error": message}
        if looks_like_oom(message):
            result["refresh_worker"] = True     # OOM: cho RunPod dựng worker mới, tránh bộ nhớ phân mảnh
        return result
    finally:
        I.free_memory(deep=True)
        I.progress.update({"running": False})


# ============================================================
# STARTUP
# ============================================================
def preload_model():
    """Nạp model lúc worker khởi động để job đầu tiên không phải chờ."""
    which = os.environ.get("RUNPOD_PRELOAD", "qwen").strip().lower()
    if which == "none":
        return
    try:
        if which == "zimage":
            I.get_zimage_pipeline()
        else:
            pipe = I.get_qwen_pipeline()
            I.set_qwen_mode(pipe, False)
        print(f"[RUNPOD] Preloaded model: {I.current_model}", flush=True)
    except Exception:
        # Không dừng worker: job đầu tiên sẽ thử nạp lại và trả lỗi rõ ràng cho client.
        traceback.print_exc()
        print("[RUNPOD] Preload thất bại - sẽ nạp lười khi có job.", flush=True)


if __name__ == "__main__":
    preload_model()
    runpod.serverless.start({"handler": handler})
