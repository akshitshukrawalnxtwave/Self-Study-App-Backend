from __future__ import annotations

import os
from pathlib import Path

from .base import WorkspaceStorage


class PrefixFilesystemStorage(WorkspaceStorage):
    """Filesystem storage with S3-like layout: {base}/{prefix}/{workspace_id}/{path}."""

    def __init__(self, base_root: Path, key_prefix: str = "workspaces") -> None:
        self.base_root = Path(base_root)
        self.key_prefix = key_prefix.strip("/")

    def _workspace_root(self, workspace_id: str) -> Path:
        """Filesystem root for a workspace: {base}/{prefix}/{workspace_id}/."""
        parts = [self.base_root]
        if self.key_prefix:
            parts.append(self.key_prefix)
        parts.append(workspace_id)
        return Path(*parts)

    def _resolve_path(self, workspace_id: str, path: str) -> Path:
        """Resolve a relative path inside the workspace, rejecting traversal."""
        if ".." in path.split("/") or path.startswith("/"):
            raise ValueError("Invalid path")
        root = self._workspace_root(workspace_id).resolve()
        full = (root / path).resolve()
        if not str(full).startswith(str(root)):
            raise ValueError("Path traversal detected")
        return full

    def ensure_workspace(self, workspace_id: str) -> None:
        # Directories are created lazily on first write.
        return None

    def read(self, workspace_id: str, path: str) -> str:
        """Read a workspace file as UTF-8 text."""
        return self.read_bytes(workspace_id, path).decode("utf-8")

    def read_bytes(self, workspace_id: str, path: str) -> bytes:
        """Read a workspace file as raw bytes; raise FileNotFoundError if missing."""
        full = self._resolve_path(workspace_id, path)
        if not full.is_file():
            raise FileNotFoundError(path)
        return full.read_bytes()

    def write(self, workspace_id: str, path: str, content: str) -> None:
        """Write UTF-8 text to a workspace file."""
        self.write_bytes(workspace_id, path, content.encode("utf-8"))

    def write_bytes(self, workspace_id: str, path: str, content: bytes) -> None:
        """Write raw bytes, creating parent directories as needed."""
        full = self._resolve_path(workspace_id, path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)

    def list(self, workspace_id: str, prefix: str) -> list[str]:
        """List relative file paths under the given prefix."""
        root = self._workspace_root(workspace_id)
        search = root / prefix if prefix else root
        if not search.exists():
            return []
        if search.is_file():
            rel = search.relative_to(root)
            return [str(rel).replace(os.sep, "/")]

        results: list[str] = []
        for dirpath, _, filenames in os.walk(search):
            for name in filenames:
                full = Path(dirpath) / name
                rel = full.relative_to(root)
                results.append(str(rel).replace(os.sep, "/"))
        return sorted(results)

    def exists(self, workspace_id: str, path: str) -> bool:
        """Check whether a workspace file exists (False on invalid paths)."""
        try:
            return self._resolve_path(workspace_id, path).is_file()
        except ValueError:
            return False

    def snapshot(self, workspace_id: str) -> dict[str, float]:
        """Return {relative_path: mtime} for all files in the workspace."""
        root = self._workspace_root(workspace_id)
        if not root.exists():
            return {}
        result: dict[str, float] = {}
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                full = Path(dirpath) / name
                rel = full.relative_to(root)
                result[str(rel).replace(os.sep, "/")] = full.stat().st_mtime
        return result
