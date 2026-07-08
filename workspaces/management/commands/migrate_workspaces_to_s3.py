from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from workspaces.storage import get_storage, is_s3_backend, reset_storage
from workspaces.storage.s3 import S3WorkspaceStorage


class Command(BaseCommand):
    help = (
        "Upload local workspaces_data/ contents to S3. "
        "Requires STORAGE_BACKEND=s3 and AWS credentials."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace-id",
            help="Migrate only this workspace ID (default: all under WORKSPACES_ROOT)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List files that would be uploaded without writing to S3",
        )

    def handle(self, *args, **options):
        if not is_s3_backend():
            raise CommandError(
                "Set STORAGE_BACKEND=s3 in .env before migrating to S3."
            )

        reset_storage()
        storage = get_storage()
        if not isinstance(storage, S3WorkspaceStorage):
            raise CommandError("Storage backend is not S3.")

        root = Path(settings.WORKSPACES_ROOT)
        if not root.exists():
            raise CommandError(f"Local workspaces root not found: {root}")

        workspace_id = options.get("workspace_id")
        dry_run = options["dry_run"]

        if workspace_id:
            dirs = [root / workspace_id]
            if not dirs[0].is_dir():
                raise CommandError(f"Workspace directory not found: {dirs[0]}")
        else:
            dirs = sorted(p for p in root.iterdir() if p.is_dir())

        total = 0
        for workspace_dir in dirs:
            ws_id = workspace_dir.name
            files = [p for p in workspace_dir.rglob("*") if p.is_file()]
            self.stdout.write(f"{ws_id}: {len(files)} file(s)")
            for path in files:
                rel = str(path.relative_to(workspace_dir)).replace("\\", "/")
                if dry_run:
                    self.stdout.write(f"  would upload {rel}")
                else:
                    storage.write_bytes(ws_id, rel, path.read_bytes())
                    self.stdout.write(f"  uploaded {rel}")
                total += 1

        action = "Would upload" if dry_run else "Uploaded"
        self.stdout.write(self.style.SUCCESS(f"{action} {total} file(s)."))
