from .local import LocalWorkspaceStorage

_storage: LocalWorkspaceStorage | None = None


def get_storage() -> LocalWorkspaceStorage:
    global _storage
    if _storage is None:
        _storage = LocalWorkspaceStorage()
    return _storage
