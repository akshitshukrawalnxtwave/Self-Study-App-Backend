from __future__ import annotations

import logging

from botocore.exceptions import ClientError
from django.conf import settings

from .base import WorkspaceStorage

logger = logging.getLogger(__name__)


def _is_not_found(exc: Exception) -> bool:
    if not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error", {})
    code = error.get("Code", "")
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchKey", "NotFound") or status == 404


class S3WorkspaceStorage(WorkspaceStorage):
    """Workspace files stored under s3://{bucket}/{prefix}/{workspace_id}/..."""

    def __init__(self) -> None:
        import boto3

        self.bucket = settings.AWS_S3_BUCKET_NAME
        if not self.bucket:
            raise ValueError(
                "AWS_S3_BUCKET_NAME is required when STORAGE_BACKEND=s3"
            )
        self.key_prefix = settings.AWS_S3_KEY_PREFIX.strip("/")
        self._client = boto3.client(
            "s3",
            region_name=settings.AWS_S3_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        )

    def _validate_path(self, path: str) -> str:
        normalized = path.strip("/")
        if not normalized:
            raise ValueError("Invalid path")
        if ".." in normalized.split("/") or path.startswith("/"):
            raise ValueError("Invalid path")
        return normalized

    def _key(self, workspace_id: str, path: str = "") -> str:
        parts = [self.key_prefix, workspace_id]
        if path:
            parts.append(self._validate_path(path))
        return "/".join(p for p in parts if p)

    def _workspace_prefix(self, workspace_id: str) -> str:
        return self._key(workspace_id) + "/"

    def ensure_workspace(self, workspace_id: str) -> None:
        # S3 has no real directories; keys are created on write.
        return None

    def read(self, workspace_id: str, path: str) -> str:
        return self.read_bytes(workspace_id, path).decode("utf-8")

    def read_bytes(self, workspace_id: str, path: str) -> bytes:
        key = self._key(workspace_id, path)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                raise FileNotFoundError(path) from exc
            raise
        return response["Body"].read()

    def write(self, workspace_id: str, path: str, content: str) -> None:
        self.write_bytes(workspace_id, path, content.encode("utf-8"))

    def write_bytes(self, workspace_id: str, path: str, content: bytes) -> None:
        key = self._key(workspace_id, path)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=content)

    def list(self, workspace_id: str, prefix: str) -> list[str]:
        workspace_prefix = self._workspace_prefix(workspace_id)
        normalized_prefix = prefix.strip("/") if prefix else ""
        search_prefix = (
            self._key(workspace_id, normalized_prefix)
            if normalized_prefix
            else workspace_prefix
        )

        results: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=search_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.startswith(workspace_prefix):
                    continue
                rel = key[len(workspace_prefix) :]
                if not rel or rel.endswith("/"):
                    continue
                if normalized_prefix:
                    if rel != normalized_prefix and not rel.startswith(
                        normalized_prefix + "/"
                    ):
                        continue
                results.append(rel)
        return sorted(set(results))

    def exists(self, workspace_id: str, path: str) -> bool:
        try:
            key = self._key(workspace_id, path)
        except ValueError:
            return False
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise

    def snapshot(self, workspace_id: str) -> dict[str, float]:
        workspace_prefix = self._workspace_prefix(workspace_id)
        result: dict[str, float] = {}
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=workspace_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(workspace_prefix) :]
                if not rel or rel.endswith("/"):
                    continue
                result[rel] = obj["LastModified"].timestamp()
        return result

    def delete(self, workspace_id: str, path: str) -> None:
        key = self._key(workspace_id, path)
        self._client.delete_object(Bucket=self.bucket, Key=key)
