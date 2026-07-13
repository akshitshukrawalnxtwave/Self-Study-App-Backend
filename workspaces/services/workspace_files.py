from __future__ import annotations

import re

from django.conf import settings


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
    return html


def apply_workspace_file_headers(response, request, *, is_html: bool) -> None:
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
