from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path

from django.conf import settings

from .agent_cache import get_agent_cache_lru
from .base import WorkspaceStorage
from .local import LocalWorkspaceStorage

logger = logging.getLogger(__name__)

_storage: WorkspaceStorage | None = None


def get_storage() -> WorkspaceStorage:
    """Return the singleton storage backend selected by STORAGE_BACKEND."""
    global _storage
    if _storage is None:
        backend = getattr(settings, "STORAGE_BACKEND", "local").lower()
        if backend == "s3":
            from .s3 import S3WorkspaceStorage

            _storage = S3WorkspaceStorage()
            logger.info(
                "Using S3WorkspaceStorage bucket=%s", settings.AWS_S3_BUCKET_NAME
            )
        elif backend == "cloud":
            from .cloud import CloudWorkspaceStorage

            _storage = CloudWorkspaceStorage()
            logger.info(
                "Using CloudWorkspaceStorage root=%s", settings.WORKSPACES_CLOUD_ROOT
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
    """True when STORAGE_BACKEND=s3."""
    return getattr(settings, "STORAGE_BACKEND", "local").lower() == "s3"


def is_cloud_backend() -> bool:
    """True when STORAGE_BACKEND=cloud (local S3 stand-in)."""
    return getattr(settings, "STORAGE_BACKEND", "local").lower() == "cloud"


def uses_remote_storage() -> bool:
    """True when durable files live in cloud or S3 (not workspaces_data alone)."""
    return is_s3_backend() or is_cloud_backend()


def uses_agent_cache() -> bool:
    """True when the agent needs a local copy under workspaces_data/."""
    return uses_remote_storage()


def local_workspace_root(workspace_id: str) -> Path:
    """Agent working copy path (workspaces_data/{workspace_id}/)."""
    return Path(settings.WORKSPACES_ROOT) / workspace_id


def agent_cache_is_warm(workspace_id: str) -> bool:
    """True if the agent cache already holds files for this workspace."""
    root = local_workspace_root(workspace_id)
    if not root.exists():
        return False
    return any(path.is_file() for path in root.rglob("*"))


def ensure_local_workspace_dirs(workspace_id: str) -> Path:
    """Return agent cwd path; subdirs are created lazily when files are written."""
    root = local_workspace_root(workspace_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def agent_cache_snapshot(workspace_id: str) -> dict[str, float]:
    """Snapshot files in the agent cache (workspaces_data/{id}/)."""
    return LocalWorkspaceStorage().snapshot(workspace_id)


def ensure_agent_cache(workspace_id: str) -> Path:
    """
    Prepare agent local cache for a remote-backed workspace.

    - If cache is warm, reuse local files.
    - Otherwise evict LRU entries when at capacity, then hydrate from remote.
    """
    lru = get_agent_cache_lru()
    lru.touch(workspace_id)

    if agent_cache_is_warm(workspace_id):
        logger.debug("Agent cache hit for workspace %s", workspace_id)
        return ensure_local_workspace_dirs(workspace_id)

    evicted = lru.evict_if_needed(workspace_id)
    if evicted:
        logger.info(
            "Evicted agent cache workspaces to make room: %s", ", ".join(evicted)
        )

    sync_remote_to_agent_cache(workspace_id)
    return ensure_local_workspace_dirs(workspace_id)


def sync_remote_to_agent_cache(
    workspace_id: str, local_root: Path | None = None
) -> None:
    """Download workspace files from cloud/S3 into workspaces_data/{id}/."""
    if not uses_remote_storage():
        return

    storage = get_storage()
    root = local_root or local_workspace_root(workspace_id)
    root.mkdir(parents=True, exist_ok=True)

    for rel_path in storage.list(workspace_id, ""):
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(storage.read_bytes(workspace_id, rel_path))

    get_agent_cache_lru().touch(workspace_id)


def sync_agent_cache_to_remote(
    workspace_id: str, local_root: Path | None = None
) -> list[str]:
    """Upload agent cache files to cloud/S3. Returns uploaded relative paths."""
    if not uses_remote_storage():
        return []

    from workspaces.services.seeding import PROTECTED_SEED_ASSET_PATHS, seed_workspace_assets

    storage = get_storage()
    root = local_root or local_workspace_root(workspace_id)
    if not root.exists():
        return []

    uploaded: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        if rel in PROTECTED_SEED_ASSET_PATHS:
            continue
        storage.write_bytes(workspace_id, rel, path.read_bytes())
        uploaded.append(rel)

    seed_workspace_assets(workspace_id)
    return uploaded


# Backwards-compatible aliases
def sync_s3_to_local(workspace_id: str, local_root: Path | None = None) -> None:
    """Deprecated alias for sync_remote_to_agent_cache()."""
    sync_remote_to_agent_cache(workspace_id, local_root)


def sync_local_to_s3(
    workspace_id: str, local_root: Path | None = None
) -> list[str]:
    """Deprecated alias for sync_agent_cache_to_remote()."""
    return sync_agent_cache_to_remote(workspace_id, local_root)


def prune_orphan_workspace_dirs() -> list[str]:
    """
    Remove workspaces_data/{id}/ folders that do not match any Workspace row.

    Prevents leftover test or stale cache directories from accumulating on disk.
    """
    from workspaces.models import Workspace

    root = Path(settings.WORKSPACES_ROOT)
    if not root.exists():
        return []

    valid_ids = {str(workspace_id) for workspace_id in Workspace.objects.values_list("id", flat=True)}
    lru = get_agent_cache_lru()
    removed: list[str] = []

    for path in root.iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            uuid.UUID(path.name)
        except ValueError:
            continue
        if path.name in valid_ids:
            continue
        shutil.rmtree(path)
        lru.remove(path.name)
        removed.append(path.name)
        logger.info("Removed orphan workspace directory %s", path.name)

    return removed
