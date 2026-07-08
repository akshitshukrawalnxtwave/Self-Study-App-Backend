from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings

from .base import WorkspaceStorage
from .local import LocalWorkspaceStorage

logger = logging.getLogger(__name__)

_storage: WorkspaceStorage | None = None


def get_storage() -> WorkspaceStorage:
    global _storage
    if _storage is None:
        backend = getattr(settings, "STORAGE_BACKEND", "local").lower()
        if backend == "s3":
            from .s3 import S3WorkspaceStorage

            _storage = S3WorkspaceStorage()
            logger.info(
                "Using S3WorkspaceStorage bucket=%s", settings.AWS_S3_BUCKET_NAME
            )
        else:
            _storage = LocalWorkspaceStorage()
            logger.info(
                "Using LocalWorkspaceStorage root=%s", settings.WORKSPACES_ROOT
            )
    return _storage


def reset_storage() -> None:
    """Clear the cached storage instance (useful in tests)."""
    global _storage
    _storage = None


def is_s3_backend() -> bool:
    return getattr(settings, "STORAGE_BACKEND", "local").lower() == "s3"


def local_workspace_root(workspace_id: str) -> Path:
    return Path(settings.WORKSPACES_ROOT) / workspace_id


def ensure_local_workspace_dirs(workspace_id: str) -> Path:
    """Ensure local agent cwd dirs exist (lessons, assets, etc.)."""
    root = local_workspace_root(workspace_id)
    for subdir in ("lessons", "reference", "learning-records", "assets"):
        (root / subdir).mkdir(parents=True, exist_ok=True)
    return root


def sync_s3_to_local(workspace_id: str, local_root: Path | None = None) -> None:
    """Download all workspace objects from S3 into a local directory for agent cwd."""
    from .s3 import S3WorkspaceStorage

    storage = get_storage()
    if not isinstance(storage, S3WorkspaceStorage):
        return

    root = local_root or local_workspace_root(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    for rel_path in storage.list(workspace_id, ""):
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(storage.read_bytes(workspace_id, rel_path))


def sync_local_to_s3(
    workspace_id: str, local_root: Path | None = None
) -> list[str]:
    """Upload all local workspace files to S3. Returns uploaded relative paths."""
    from .s3 import S3WorkspaceStorage

    storage = get_storage()
    if not isinstance(storage, S3WorkspaceStorage):
        return []

    root = local_root or local_workspace_root(workspace_id)
    if not root.exists():
        return []

    uploaded: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        storage.write_bytes(workspace_id, rel, path.read_bytes())
        uploaded.append(rel)
    return uploaded
