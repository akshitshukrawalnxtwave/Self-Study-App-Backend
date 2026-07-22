from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import jwt
from django.conf import settings
from django.http import HttpRequest

from workspaces.utils import error_response

if TYPE_CHECKING:
    from workspaces.models import Workspace


def _jwt_secret() -> str:
    return getattr(settings, "JWT_SECRET_KEY", settings.SECRET_KEY)


def get_request_user_id(request: HttpRequest) -> uuid.UUID | None:
    """Resolve the caller's user id from Bearer JWT or session cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            user_id = _user_id_from_jwt(token)
            if user_id is not None:
                return user_id

    # Cross-origin clients send their per-browser id here (the user_id cookie
    # cannot cross domains). Client-asserted, unverified — POC-level separation.
    header_value = request.headers.get("X-User-Id", "").strip()
    if header_value:
        try:
            return uuid.UUID(header_value)
        except ValueError:
            return None

    cookie_value = request.COOKIES.get("user_id", "").strip()
    if cookie_value:
        try:
            return uuid.UUID(cookie_value)
        except ValueError:
            return None

    return None


def _user_id_from_jwt(token: str) -> uuid.UUID | None:
    try:
        payload = jwt.decode(
            token,
            _jwt_secret(),
            algorithms=["HS256"],
            options={"require": ["sub"]},
        )
    except jwt.PyJWTError:
        return None

    subject = payload.get("sub")
    if not subject:
        return None
    try:
        return uuid.UUID(str(subject))
    except ValueError:
        return None


def require_authenticated_user(request: HttpRequest):
    """
    Resolve the caller's user id.

    When WORKSPACE_AUTH_REQUIRED is on and identity is missing, return
    (None, 401 response). Otherwise return (user_id | None, None).
    """
    user_id = get_request_user_id(request)
    if getattr(settings, "WORKSPACE_AUTH_REQUIRED", False) and user_id is None:
        return None, error_response("Not authenticated", "UNAUTHORIZED", 401)
    return user_id, None


def require_workspace_access(request: HttpRequest, workspace: Workspace):
    """
    Return an error JsonResponse when auth is required and access is denied.
    Returns None when the request may proceed.
    """
    if not getattr(settings, "WORKSPACE_AUTH_REQUIRED", False):
        return None

    user_id = get_request_user_id(request)
    if user_id is None:
        return error_response("Not authenticated", "UNAUTHORIZED", 401)

    if workspace.user_id and workspace.user_id != user_id:
        return error_response("Forbidden", "FORBIDDEN", 403)

    return None
