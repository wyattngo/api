# Cutin image worker (`api`)

A [Runpod Serverless](https://docs.runpod.io/serverless/overview) worker that generates and edits product images for Cutin. It wraps `inference.py` (Z-Image-Turbo, Qwen-Image, optional FLUX.2 klein, and rembg background removal) behind a single JSON handler.

Runpod queues the jobs and each worker runs one job at a time, so there is no web server or GPU queue here. The handler calls the functions in `inference.py` directly.

## Contents

| Path | Purpose |
|---|---|
| `runpod_handler.py` | Worker entry point. Parses `job["input"]`, runs the action, packages images. Its docstring is the full request/response reference. |
| `inference.py` | Model loading and generation: text-to-image, edit, transparent, compose, and the 8-case product batch. |
| `product_category.py` | Product categories that tune the extra sentence in each batch case. |
| `bucket_upload.py` | Uploads results to an R2/S3 bucket under `<job_id>/<index>.<ext>`. |
| `diffusers/` | Local diffusers source, installed into the image. |
| `Dockerfile` | Production image (Z-Image-Turbo and rembg baked in). |
| `Dockerfile.flux` | Trial image with FLUX.2 klein 4B baked in. |
| `tests/` | Unit tests (`test_bucket_upload.py`, `test_product_category.py`, `test_layout_lock.py`, `test_klein_branch.py`). |

## Actions

Send `{"input": {"action": "...", ...}}`. `action` defaults to `generate`.

| `action` | What it does |
|---|---|
| `generate` | One image. `mode`: `text`, `edit`, `transparent`, `compose`. `quality`: `fast` (default) or `fine`. |
| `batch` | The 8-case product shot set from 1 to 10 reference images. Takes `category`, `description`, `mode` (`draft` or `final`). |
| `rerender` | Re-render chosen cases (1 to 8) at final quality. The worker keeps no state, so the client resends `images`, `params` and `prompts` from the batch result. |
| `health` | Device, dtype, loaded model, GPU memory, and whether the bucket is configured. |

How `quality` maps to a model, for `generate`:

| `mode` | `fast` | `fine` |
|---|---|---|
| `text` | Z-Image (9 fixed steps) | Qwen text-to-image |
| `edit` | Z-Image image-to-image (1 image) | Qwen |
| `transparent` | rembg cutout only | Qwen RGBA |
| `compose` | not available | Qwen, 2 to 10 images |

Common fields: `output` (`auto`, `base64`, `s3`), `image_format` (`png`, `webp`, `jpeg`), `images` (base64, data URI, or http(s) URL, up to 10), `aspect_ratio`, `resolution` (1024, 1536, 2048), `steps` (20, 30, 40), `seed` (-1 = random; the real seed is returned).

### Example

```bash
curl -s -X POST "https://api.runpod.ai/v2/$ENDPOINT_ID/runsync" \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": {"action": "generate", "prompt": "a ceramic mug on a wooden table", "mode": "text", "quality": "fast", "output": "s3"}}'
```

A success response carries `image` with `format`, `mime`, `bytes` and either `base64` or `url`. A failure returns `{"error": "..."}`. If the error looks like out-of-memory or a CUDA fatal error, the response also has `"refresh_worker": true` so Runpod replaces the worker.

## Output and the R2 bucket

With `output: "auto"`, the worker uploads to the bucket when it is configured and otherwise returns base64.

Runpod caps responses at 10 MB for `/run` and 20 MB for `/runsync`. A batch of 8 images almost always exceeds that as base64, so **configure the bucket for batch use**. Without a bucket, the handler switches PNG to WebP when the total passes `RUNPOD_MAX_INLINE_MB`, and returns a clear error if it is still too large.

Set these on the Runpod endpoint (never commit values):

| Variable | Meaning |
|---|---|
| `BUCKET_NAME` | Bucket to write to. Defaults to `img` if unset; production uses `cutin-images`. |
| `BUCKET_ENDPOINT_URL` | S3-compatible endpoint (R2). |
| `BUCKET_ACCESS_KEY_ID` | Access key. |
| `BUCKET_SECRET_ACCESS_KEY` | Secret key. |

Objects are written to `<job_id>/<index>.<ext>` so the reader (`cutin-control`) can derive the location from the job id. The returned `url` is a presigned GET valid for 7 days. We do not use the SDK's `rp_upload.upload_image`, because it writes to a `MM-YY` bucket with a random key, which the reader cannot find.

## Other configuration

Defaults baked into `Dockerfile`: `APP_DEVICE=cuda`, `APP_DTYPE=bf16`, `APP_CPU_OFFLOAD=1` (24 GB card), `APP_TURBO=1`, `APP_PROMPT_ENHANCER=1`.

| Variable | Default | Meaning |
|---|---|---|
| `RUNPOD_PRELOAD` | `qwen` | Model loaded at worker start: `qwen`, `zimage`, `flux2klein`, `none`. |
| `RUNPOD_MAX_INLINE_MB` | `9` | Cap on total base64 in one response. |
| `RUNPOD_ALLOW_URL_INPUT` | `1` | Allow URL inputs in `images`. Private and internal addresses are blocked. |
| `RUNPOD_MAX_DOWNLOAD_MB` | `40` | Cap per downloaded image. |
| `RUNPOD_PROGRESS_SECS` | `2` | Interval of `progress_update`. |
| `RUNPOD_KEEP_FILES` | `0` | `1` keeps intermediate files on the worker disk. |
| `APP_CPU_OFFLOAD` | `1` | Set `0` on 80 GB GPUs (A100/H100) to keep the model on the GPU. |

Do not override `HF_HOME` or `U2NET_HOME` on the endpoint: the baked models live there.

### Models

- **Baked into the image:** Z-Image-Turbo (Apache-2.0) and the rembg `isnet-general-use` model, so cold start does not download them.
- **Not baked in:** Qwen-Image, because of its Qwen Research License. Use Runpod [Cached Models](https://docs.runpod.io/serverless/endpoints/model-caching) for it. The worker finds it under `/runpod-volume/huggingface-cache/hub` and falls back to the normal Hugging Face cache.

## Build and deploy

Images are built by GitHub Actions and pushed to Docker Hub. Both workflows are **manual only** (`workflow_dispatch`); a push to `main` does not build.

| Workflow | Dockerfile | Image |
|---|---|---|
| **Build and Push Runpod Image** (`docker.yml`) | `Dockerfile` | `36234138/runpod-cutin:<sha>` and `:latest` |
| **Build and Push FLUX klein trial image** (`docker-flux.yml`) | `Dockerfile.flux` | `36234138/runpod-cutin-flux:<sha>` and `:latest` |

To ship a change:

1. Merge to `main`.
2. In GitHub, go to **Actions**, choose **Build and Push Runpod Image**, and click **Run workflow** on `main`.
3. Point the Runpod endpoint at `36234138/runpod-cutin:<commit sha>`. Prefer the sha tag over `latest` so a rollback is one edit.

Repository settings the workflows need: variable `DOCKERHUB_USERNAME`, secret `DOCKERHUB_TOKEN`, and optionally secret `HF_TOKEN` for the FLUX build if the model repo is gated (passed as a build secret, not kept in a layer).

## Local development

The worker needs a CUDA GPU to run real generation. To exercise the handler without the server:

```bash
pip install -r requirements.txt            # plus: pip install ./diffusers
python runpod_handler.py --test_input '{"input": {"action": "health"}}'
```

Run the tests with `pytest tests/`.

Set `RUNPOD_PRELOAD=none` to skip loading a model when you only need to check input handling.
