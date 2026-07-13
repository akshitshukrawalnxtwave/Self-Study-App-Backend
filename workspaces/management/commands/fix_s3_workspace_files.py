from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from workspaces.models import Workspace
from workspaces.services.seeding import seed_workspace_assets
from workspaces.storage import get_storage, is_s3_backend, reset_storage
from workspaces.storage.s3 import S3WorkspaceStorage


class Command(BaseCommand):
    help = (
        "Repair S3 workspace files: restore seed assets, fix Content-Type metadata, "
        "and refresh presigned CSS/JS links inside lesson HTML."
    )

    def add_arguments(self, parser):
        """Register --workspace-id and --dry-run options."""
        parser.add_argument(
            "--workspace-id",
            help="Fix only this workspace ID (default: all workspaces in DB)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List files that would be repaired without writing to S3",
        )

    def handle(self, *args, **options):
        """Re-seed assets, fix Content-Type, and refresh lesson HTML links on S3."""
        if not is_s3_backend():
            raise CommandError("Set STORAGE_BACKEND=s3 in .env before running this command.")

        reset_storage()
        storage = get_storage()
        if not isinstance(storage, S3WorkspaceStorage):
            raise CommandError("Storage backend is not S3.")

        workspace_id = options.get("workspace_id")
        dry_run = options["dry_run"]

        if workspace_id:
            if not Workspace.objects.filter(pk=workspace_id).exists():
                raise CommandError(f"Workspace not found in DB: {workspace_id}")
            workspaces = [workspace_id]
        else:
            workspaces = [
                str(workspace_id)
                for workspace_id in Workspace.objects.values_list("id", flat=True)
            ]

        total = 0
        for ws_id in workspaces:
            paths = storage.list(ws_id, "")
            self.stdout.write(f"{ws_id}: {len(paths)} file(s)")
            if dry_run:
                for path in paths:
                    self.stdout.write(f"  would repair {path}")
                total += len(paths)
                continue

            seed_workspace_assets(ws_id)
            for path in paths:
                if path.startswith("lessons/") and path.endswith(".html"):
                    storage.refresh_lesson_html_urls(ws_id, path)
                elif path.startswith("assets/"):
                    continue
                else:
                    storage.fix_object_metadata(ws_id, path)
                self.stdout.write(f"  repaired {path}")
                total += 1

        action = "Would repair" if dry_run else "Repaired"
        self.stdout.write(self.style.SUCCESS(f"{action} {total} file(s)."))
