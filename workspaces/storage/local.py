import os
from pathlib import Path

from django.conf import settings

from .base import WorkspaceStorage


class LocalWorkspaceStorage(WorkspaceStorage):
    def _workspace_root(self, workspace_id: str) -> Path:
        return Path(settings.WORKSPACES_ROOT) / workspace_id

    def _resolve_path(self, workspace_id: str, path: str) -> Path:
        if ".." in path.split("/") or path.startswith("/"):
            raise ValueError("Invalid path")
        root = self._workspace_root(workspace_id).resolve()
        full = (root / path).resolve()
        if not str(full).startswith(str(root)):
            raise ValueError("Path traversal detected")
        return full

    def ensure_workspace(self, workspace_id: str) -> None:
        root = self._workspace_root(workspace_id)
        for subdir in ("lessons", "reference", "learning-records", "assets"):
            (root / subdir).mkdir(parents=True, exist_ok=True)

    def read(self, workspace_id: str, path: str) -> str:
        full = self._resolve_path(workspace_id, path)
        return full.read_text(encoding="utf-8")

    def write(self, workspace_id: str, path: str, content: str) -> None:
        full = self._resolve_path(workspace_id, path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    def list(self, workspace_id: str, prefix: str) -> list[str]:
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
        try:
            return self._resolve_path(workspace_id, path).exists()
        except ValueError:
            return False

    def snapshot(self, workspace_id: str) -> dict[str, float]:
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

    def read_bytes(self, workspace_id: str, path: str) -> bytes:
        full = self._resolve_path(workspace_id, path)
        return full.read_bytes()
