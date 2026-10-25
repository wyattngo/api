"""Ghi ảnh job vào bucket R2/S3 mà bên đọc (cutin-control) suy ra được từ job_id.

SDK `rp_upload.upload_image` không đọc tên bucket từ env: nó ghi vào bucket "MM-YY" với key
`<job_id>/<8 ký tự ngẫu nhiên>`, nên GET /v1/jobs/<id>/image không tìm lại được -> 404.
Ở đây: bucket = BUCKET_NAME (mặc định "img"), key = `<job_id>/<index>.<ext>`.
"""
import os
from pathlib import Path

DEFAULT_BUCKET = "img"


def _client():
    from runpod.serverless.utils import rp_upload
    client, _ = rp_upload.get_boto_client()
    return client


def upload(job_id, index, path, mime):
    """-> (bucket, key, presigned_url)"""
    path = Path(path)
    bucket = os.environ.get("BUCKET_NAME") or DEFAULT_BUCKET
    key = f"{job_id}/{index}{path.suffix}"
    client = _client()
    client.put_object(Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType=mime)
    url = client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=604800)
    return bucket, key, url
