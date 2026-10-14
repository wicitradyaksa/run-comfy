"""Optional S3 / R2 / MinIO upload of finished exports. Credentials come from the standard AWS env vars."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from .config import StorageCfg


class Storage:
    def __init__(self, cfg: StorageCfg, client=None):
        self.cfg = cfg
        self._client = client

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.s3_bucket)

    def client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client("s3", endpoint_url=self.cfg.s3_endpoint_url or None)
        return self._client

    def upload(self, path: Path, key: str) -> str:
        """Upload and return a URL (public base URL if configured, else a 7-day presigned URL)."""
        full = self.cfg.s3_prefix + key
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.client().upload_file(str(path), self.cfg.s3_bucket, full, ExtraArgs={"ContentType": ctype})
        if self.cfg.public_base_url:
            return self.cfg.public_base_url.rstrip("/") + "/" + full
        return self.client().generate_presigned_url(
            "get_object", Params={"Bucket": self.cfg.s3_bucket, "Key": full}, ExpiresIn=7 * 24 * 3600)
