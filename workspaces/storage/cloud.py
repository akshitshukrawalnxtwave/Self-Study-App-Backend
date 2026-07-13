from __future__ import annotations

import logging

from django.conf import settings

from .prefix_fs import PrefixFilesystemStorage

logger = logging.getLogger(__name__)


class CloudWorkspaceStorage(PrefixFilesystemStorage):
    """Local filesystem stand-in for S3 (for testing before AWS is configured)."""

    def __init__(self) -> None:
        super().__init__(
            base_root=settings.WORKSPACES_CLOUD_ROOT,
            key_prefix=settings.AWS_S3_KEY_PREFIX,
        )
        logger.debug(
            "CloudWorkspaceStorage root=%s prefix=%s",
            settings.WORKSPACES_CLOUD_ROOT,
            settings.AWS_S3_KEY_PREFIX,
        )
