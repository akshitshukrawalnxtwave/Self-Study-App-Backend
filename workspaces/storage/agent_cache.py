from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

LRU_STATE_FILENAME = ".agent_cache_lru.json"


class AgentCacheLRU:
    """Track recently used agent cache workspaces; evict oldest when at capacity."""

    def __init__(self, cache_root: Path, max_size: int) -> None:
        self.cache_root = cache_root
        self.max_size = max_size
        self.state_path = cache_root / LRU_STATE_FILENAME

    def _load(self) -> dict[str, float]:
        """Load {workspace_id: last_used_ts} state from disk (empty on error)."""
        if not self.state_path.is_file():
            return {}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): float(v) for k, v in data.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            logger.warning("Invalid agent cache LRU state; resetting")
        return {}

    def _save(self, state: dict[str, float]) -> None:
        """Persist the LRU state file to the cache root."""
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def touch(self, workspace_id: str) -> None:
        """Mark a workspace as recently used."""
        state = self._load()
        state[workspace_id] = time.time()
        self._save(state)

    def remove(self, workspace_id: str) -> None:
        """Drop a workspace from the LRU state (e.g. after deletion)."""
        state = self._load()
        if workspace_id in state:
            del state[workspace_id]
            self._save(state)

    def evict_if_needed(self, workspace_id: str) -> list[str]:
        """Evict least-recently-used cached workspaces until there is room."""
        state = self._load()
        state[workspace_id] = time.time()

        cached_ids = [
            p.name
            for p in self.cache_root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ]

        evicted: list[str] = []
        while len(cached_ids) >= self.max_size and workspace_id not in cached_ids:
            if not state:
                break
            lru_id = min(state, key=lambda wid: state[wid])
            if lru_id == workspace_id:
                break
            self._evict_workspace(lru_id)
            evicted.append(lru_id)
            state.pop(lru_id, None)
            cached_ids = [
                p.name
                for p in self.cache_root.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            ]

        self._save(state)
        return evicted

    def _evict_workspace(self, workspace_id: str) -> None:
        """Delete a workspace's cached files from disk."""
        target = self.cache_root / workspace_id
        if target.exists():
            shutil.rmtree(target)
            logger.info("Evicted agent cache workspace %s", workspace_id)


def get_agent_cache_lru() -> AgentCacheLRU:
    """Build the LRU tracker for the agent cache using configured limits."""
    max_size = getattr(settings, "WORKSPACE_AGENT_CACHE_MAX_SIZE", 10)
    return AgentCacheLRU(Path(settings.WORKSPACES_ROOT), max_size)
