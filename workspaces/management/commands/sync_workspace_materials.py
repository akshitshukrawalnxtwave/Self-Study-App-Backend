from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from workspaces.models import Workspace
from workspaces.services.materials import sync_materials_from_storage
from workspaces.storage import get_storage, reset_storage


class Command(BaseCommand):
    help = "Sync learning material metadata from storage (S3/local) into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--workspace-id",
            help="Sync only this workspace ID (default: all workspaces in DB)",
        )

    def handle(self, *args, **options):
        reset_storage()
        get_storage()

        workspace_id = options.get("workspace_id")
        if workspace_id:
            try:
                workspaces = [Workspace.objects.get(pk=workspace_id)]
            except Workspace.DoesNotExist as exc:
                raise CommandError(f"Workspace not found in DB: {workspace_id}") from exc
        else:
            workspaces = list(Workspace.objects.all())

        total = 0
        for workspace in workspaces:
            materials = sync_materials_from_storage(workspace)
            self.stdout.write(f"{workspace.id}: synced {len(materials)} material(s)")
            for material in materials:
                self.stdout.write(f"  {material.kind} {material.path}")
            total += len(materials)

        self.stdout.write(self.style.SUCCESS(f"Synced {total} material(s)."))
