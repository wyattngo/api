"""Ảnh job phải nằm ở bucket/key mà bên đọc suy ra được từ job_id."""
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bucket_upload  # noqa: E402


class FakeClient:
    def __init__(self):
        self.puts = []

    def put_object(self, **kw):
        self.puts.append(kw)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://r2.example/{Params['Bucket']}/{Params['Key']}?sig=x"


def _run(tmp_path, env):
    f = tmp_path / "out.png"
    f.write_bytes(b"\x89PNG fake")
    client = FakeClient()
    with mock.patch.object(bucket_upload, "_client", return_value=client), \
            mock.patch.dict(os.environ, env, clear=False):
        if "BUCKET_NAME" not in env:
            os.environ.pop("BUCKET_NAME", None)
        result = bucket_upload.upload("job-123", 0, f, "image/png")
    return result, client


def test_default_bucket_is_img_and_key_is_deterministic(tmp_path):
    (bucket, key, url), client = _run(tmp_path, {})
    assert client.puts[0]["Bucket"] == "img"
    assert client.puts[0]["Key"] == "job-123/0.png"
    assert (bucket, key) == ("img", "job-123/0.png")
    assert "/img/job-123/0.png" in url


def test_bucket_name_env_overrides(tmp_path):
    (bucket, _, url), client = _run(tmp_path, {"BUCKET_NAME": "other"})
    assert client.puts[0]["Bucket"] == "other"
    assert "/other/job-123/0.png" in url
