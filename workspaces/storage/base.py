from abc import ABC, abstractmethod

from django.conf import settings


class WorkspaceStorage(ABC):
    """Abstract interface for workspace file storage (local, cloud, or S3)."""

    @abstractmethod
    def read(self, workspace_id: str, path: str) -> str:
        """Read a workspace file as UTF-8 text."""
        ...

    @abstractmethod
    def write(self, workspace_id: str, path: str, content: str) -> None:
        """Write UTF-8 text to a workspace file, creating parents as needed."""
        ...

    @abstractmethod
    def list(self, workspace_id: str, prefix: str) -> list[str]:
        """List relative file paths under the given prefix ('' for all files)."""
        ...

    @abstractmethod
    def exists(self, workspace_id: str, path: str) -> bool:
        """Check whether a workspace file exists."""
        ...

    @abstractmethod
    def ensure_workspace(self, workspace_id: str) -> None:
        """Prepare backend state for a workspace (no-op for most backends)."""
        ...

    @abstractmethod
    def snapshot(self, workspace_id: str) -> dict[str, float]:
        """Return {relative_path: mtime} for all files in the workspace."""
        ...

    @abstractmethod
    def read_bytes(self, workspace_id: str, path: str) -> bytes:
        """Read a workspace file as raw bytes."""
        ...

    def file_url(self, workspace_id: str, path: str) -> str:
        """Public proxy URL for a workspace file."""
        normalized = path.strip("/")
        rel = f"/workspaces/{workspace_id}/{normalized}"
        base = getattr(settings, "WORKSPACES_PUBLIC_BASE_URL", "").rstrip("/")
        if base:
            return f"{base}{rel}"
        return rel
