from __future__ import annotations

import logging
import mimetypes
import re

from botocore.exceptions import ClientError
from django.conf import settings
import boto3

from .base import WorkspaceStorage

logger = logging.getLogger(__name__)


def _is_not_found(exc: Exception) -> bool:
    """True when a boto3 ClientError means the S3 object doesn't exist."""
    if not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error", {})
    code = error.get("Code", "")
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return code in ("404", "NoSuchKey", "NotFound") or status == 404


class S3WorkspaceStorage(WorkspaceStorage):
    """Workspace files stored under s3://{bucket}/{prefix}/{workspace_id}/..."""

    def __init__(self) -> None:
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
        """Normalize a relative path and reject traversal or empty paths."""
        normalized = path.strip("/")
        if not normalized:
            raise ValueError("Invalid path")
        if ".." in normalized.split("/") or path.startswith("/"):
            raise ValueError("Invalid path")
        return normalized

    def _key(self, workspace_id: str, path: str = "") -> str:
        """Build the full S3 object key: {prefix}/{workspace_id}/{path}."""
        parts = [self.key_prefix, workspace_id]
        if path:
            parts.append(self._validate_path(path))
        return "/".join(p for p in parts if p)

    def _workspace_prefix(self, workspace_id: str) -> str:
        """Key prefix covering all objects in a workspace."""
        return self._key(workspace_id) + "/"

    def _presigned_url(self, workspace_id: str, path: str) -> str:
        """Generate a time-limited presigned GET URL for a workspace file."""
        key = self._key(workspace_id, path)
        expiry = getattr(settings, "AWS_S3_PRESIGNED_URL_EXPIRY_SECONDS", 3600)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expiry,
        )

    def _rewrite_html_asset_refs(self, workspace_id: str, content: bytes) -> bytes:
        """Embed fresh presigned S3 URLs for shared CSS/JS assets."""
        html = content.decode("utf-8")
        css_url = self._presigned_url(workspace_id, "assets/lesson.css")
        js_url = self._presigned_url(workspace_id, "assets/quiz.js")

        if re.search(r"lesson\.css", html, flags=re.IGNORECASE):
            html = re.sub(
                r'href=["\'][^"\']*lesson\.css(?:\?[^"\']*)?["\']',
                f'href="{css_url}"',
                html,
                count=1,
                flags=re.IGNORECASE,
            )
        elif re.search(r"</head>", html, flags=re.IGNORECASE):
            html = re.sub(
                r"</head>",
                f'  <link rel="stylesheet" href="{css_url}">\n</head>',
                html,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            html = f'<link rel="stylesheet" href="{css_url}">\n{html}'

        if re.search(r"quiz\.js", html, flags=re.IGNORECASE):
            html = re.sub(
                r'src=["\'][^"\']*quiz\.js(?:\?[^"\']*)?["\']',
                f'src="{js_url}"',
                html,
                count=1,
                flags=re.IGNORECASE,
            )
        elif re.search(r"</body>", html, flags=re.IGNORECASE):
            html = re.sub(
                r"</body>",
                f'  <script src="{js_url}"></script>\n</body>',
                html,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            html = f'{html}\n<script src="{js_url}"></script>'

        return html.encode("utf-8")

    def refresh_lesson_html_urls(self, workspace_id: str, path: str) -> None:
        """Rewrite lesson HTML in S3 with fresh presigned asset URLs."""
        normalized = self._validate_path(path)
        if not normalized.endswith(".html") or not self.exists(workspace_id, normalized):
            return
        content = self.read_bytes(workspace_id, normalized)
        self.write_bytes(workspace_id, normalized, content)

    def _content_type(self, path: str) -> str:
        """Pick the Content-Type S3 should serve for a file (charset included)."""
        lower = path.lower()
        if lower.endswith(".css"):
            return "text/css; charset=utf-8"
        if lower.endswith(".js"):
            return "application/javascript; charset=utf-8"
        if lower.endswith(".html"):
            return "text/html; charset=utf-8"
        if lower.endswith(".md"):
            return "text/markdown; charset=utf-8"
        if lower.endswith(".json"):
            return "application/json; charset=utf-8"

        content_type, _ = mimetypes.guess_type(path)
        if content_type == "text/javascript":
            return "application/javascript; charset=utf-8"
        if content_type and (
            content_type.startswith("text/") or content_type == "application/json"
        ):
            return f"{content_type}; charset=utf-8"
        return content_type or "application/octet-stream"

    def ensure_workspace(self, workspace_id: str) -> None:
        """No-op: S3 has no real directories; keys are created on write."""
        return None

    def read(self, workspace_id: str, path: str) -> str:
        """Read a workspace object as UTF-8 text."""
        return self.read_bytes(workspace_id, path).decode("utf-8")

    def read_bytes(self, workspace_id: str, path: str) -> bytes:
        """Download an object's bytes; raise FileNotFoundError if the key is missing."""
        key = self._key(workspace_id, path)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                raise FileNotFoundError(path) from exc
            raise
        return response["Body"].read()

    def write(self, workspace_id: str, path: str, content: str) -> None:
        """Upload UTF-8 text as a workspace object."""
        self.write_bytes(workspace_id, path, content.encode("utf-8"))

    def write_bytes(self, workspace_id: str, path: str, content: bytes) -> None:
        """Upload bytes with the correct Content-Type; HTML gets asset URLs rewritten."""
        normalized = self._validate_path(path)
        key = self._key(workspace_id, normalized)
        if normalized.endswith(".html"):
            content = self._rewrite_html_asset_refs(workspace_id, content)

        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=self._content_type(normalized),
        )

    def fix_object_metadata(self, workspace_id: str, path: str) -> None:
        """Re-upload an object so S3 metadata (Content-Type, HTML asset URLs) is correct."""
        normalized = self._validate_path(path)
        if not self.exists(workspace_id, normalized):
            return
        self.write_bytes(workspace_id, normalized, self.read_bytes(workspace_id, normalized))

    def list(self, workspace_id: str, prefix: str) -> list[str]:
        """List relative object paths under the given prefix ('' for all files)."""
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
        """Check object existence via HEAD (False on invalid paths)."""
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
        """Return {relative_path: last_modified_timestamp} for all objects."""
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
        """Delete a workspace object from S3."""
        key = self._key(workspace_id, path)
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def file_url(self, workspace_id: str, path: str) -> str:
        """Presigned S3 URL for browser access to private bucket objects."""
        normalized = path.strip("/")
        return self._presigned_url(workspace_id, normalized)
