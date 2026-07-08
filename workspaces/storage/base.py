from abc import ABC, abstractmethod


class WorkspaceStorage(ABC):
    @abstractmethod
    def read(self, workspace_id: str, path: str) -> str:
        ...

    @abstractmethod
    def write(self, workspace_id: str, path: str, content: str) -> None:
        ...

    @abstractmethod
    def list(self, workspace_id: str, prefix: str) -> list[str]:
        ...

    @abstractmethod
    def exists(self, workspace_id: str, path: str) -> bool:
        ...

    @abstractmethod
    def ensure_workspace(self, workspace_id: str) -> None:
        ...

    @abstractmethod
    def snapshot(self, workspace_id: str) -> dict[str, float]:
        """Return {relative_path: mtime} for all files in the workspace."""
        ...
