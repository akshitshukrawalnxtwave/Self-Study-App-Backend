from __future__ import annotations

import re
from urllib.parse import unquote

from django.conf import settings

ALLOWED_WORKSPACE_FILE_PREFIXES = (
    "lessons/",
    "reference/",
    "assets/",
    "learning-records/",
)

ALLOWED_ROOT_MARKDOWN_FILES = frozenset({"RESOURCES.md", "NOTES.md"})


def validate_workspace_file_path(file_path: str) -> str | None:
    """
    Normalize and validate a workspace file path for the file proxy.
    Returns None when the path is invalid or not under an allowed prefix.
    """
    if not file_path or "\x00" in file_path:
        return None

    decoded = unquote(file_path).replace("\\", "/")
    if decoded.startswith("/"):
        return None

    normalized = decoded.strip("/")
    if not normalized:
        return None

    parts = normalized.split("/")
    if ".." in parts or any(part == "" for part in parts):
        return None

    if not any(normalized.startswith(prefix) for prefix in ALLOWED_WORKSPACE_FILE_PREFIXES):
        if normalized not in ALLOWED_ROOT_MARKDOWN_FILES:
            return None

    if normalized.startswith("learning-records/") and not normalized.endswith(".md"):
        return None

    return normalized


def content_type_for_file_path(file_path: str) -> str:
    """Map workspace file extensions to response Content-Type values."""
    lower = file_path.lower()
    if lower.endswith(".html"):
        return "text/html; charset=utf-8"
    if lower.endswith(".css"):
        return "text/css; charset=utf-8"
    if lower.endswith(".js"):
        return "application/javascript; charset=utf-8"
    if lower.endswith(".md"):
        return "text/plain; charset=utf-8"

    import mimetypes

    content_type, _ = mimetypes.guess_type(file_path)
    if content_type is None:
        return "application/octet-stream"
    if content_type.startswith("text/") or content_type in (
        "application/javascript",
        "application/json",
    ):
        return f"{content_type}; charset=utf-8"
    return content_type


def workspace_asset_base(workspace_id: str) -> str:
    """Root-relative or absolute base for workspace asset URLs."""
    path = f"/workspaces/{workspace_id}/assets/"
    public = getattr(settings, "WORKSPACES_PUBLIC_BASE_URL", "").rstrip("/")
    if public:
        return f"{public}{path}"
    return path


def rewrite_workspace_asset_refs(html: str, workspace_id: str) -> str:
    """
    Rewrite relative asset links to /workspaces/{id}/assets/... so CSS/JS load
    via the Vite proxy (or Django directly) regardless of iframe origin.
    """
    asset_base = workspace_asset_base(workspace_id)
    html = re.sub(
        r'((?:href|src)=["\'])\.\./assets/',
        rf"\1{asset_base}",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r'((?:href|src)=["\'])assets/',
        rf"\1{asset_base}",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        rf'((?:href|src)=["\'])https?://[^"\']*/workspaces/{re.escape(workspace_id)}/assets/([^"?]+)(?:\?[^"\']*)?(["\'])',
        rf"\1{asset_base}\2\3",
        html,
        flags=re.IGNORECASE,
    )
    return html


def apply_workspace_file_headers(
    response, request, *, is_html: bool, file_path: str = ""
) -> None:
    """CORS + iframe embedding for workspace files loaded by the frontend."""
    origin = request.headers.get("Origin")
    allowed = getattr(settings, "CORS_ALLOWED_ORIGINS", [])
    if origin and origin in allowed:
        response["Access-Control-Allow-Origin"] = origin
        response["Vary"] = "Origin"

    if is_html:
        ancestors = ["'self'", *allowed]
        response["Content-Security-Policy"] = (
            "frame-ancestors " + " ".join(ancestors)
        )
    elif file_path.startswith("assets/"):
        response["Cache-Control"] = "public, max-age=3600"
