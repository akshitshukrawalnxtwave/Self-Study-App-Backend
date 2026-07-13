from __future__ import annotations

from workspaces.storage import get_storage


def workspace_file_url(workspace_id: str, path: str) -> str:
    """Return the frontend-facing URL for a workspace file."""
    return get_storage().file_url(workspace_id, path)
