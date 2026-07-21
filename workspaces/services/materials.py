from __future__ import annotations

from workspaces.models import LearningMaterial, Workspace
from workspaces.storage import get_storage
from workspaces.storage.urls import workspace_file_url
from workspaces.utils import material_title_from_path

KIND_ORDER = ("reference", "learning_record", "resource")

ROOT_RESOURCE_FILES = frozenset({"RESOURCES.md", "NOTES.md"})


def classify_material_path(path: str) -> tuple[str, str] | None:
    """Map a workspace-relative path to (kind, format), or None if not material."""
    if path.startswith("reference/") and path.endswith(".html"):
        return "reference", "html"
    if path.startswith("learning-records/") and path.endswith(".md"):
        return "learning_record", "markdown"
    if path in ROOT_RESOURCE_FILES:
        return "resource", "markdown"
    return None


def _material_paths_from_storage(workspace_id: str) -> list[str]:
    storage = get_storage()
    paths: set[str] = set()

    for prefix in ("reference", "learning-records"):
        paths.update(storage.list(workspace_id, prefix))

    for root_file in ROOT_RESOURCE_FILES:
        if storage.exists(workspace_id, root_file):
            paths.add(root_file)

    return sorted(paths)


def sync_materials_from_storage(workspace: Workspace) -> list[LearningMaterial]:
    """Import learning material files from storage into the DB (backfill / repair)."""
    workspace_id = str(workspace.id)
    paths = _material_paths_from_storage(workspace_id)

    synced: list[LearningMaterial] = []
    seen_paths: set[str] = set()
    for path in paths:
        classified = classify_material_path(path)
        if classified is None:
            continue
        kind, file_format = classified
        seen_paths.add(path)
        material, _ = LearningMaterial.objects.update_or_create(
            workspace=workspace,
            path=path,
            defaults={
                "kind": kind,
                "format": file_format,
                "title": material_title_from_path(path),
            },
        )
        synced.append(material)

    LearningMaterial.objects.filter(workspace=workspace).exclude(path__in=seen_paths).delete()
    return synced


def register_materials_from_artifacts(
    workspace: Workspace, artifacts: list[dict]
) -> list[LearningMaterial]:
    """Create or update LearningMaterial rows from agent turn artifacts."""
    updated: list[LearningMaterial] = []
    for artifact in artifacts:
        if artifact.get("type") != "reference":
            continue
        path = artifact.get("path")
        if not path:
            continue
        classified = classify_material_path(path)
        if classified is None:
            continue
        kind, file_format = classified
        material, _ = LearningMaterial.objects.update_or_create(
            workspace=workspace,
            path=path,
            defaults={
                "kind": kind,
                "format": file_format,
                "title": material_title_from_path(path),
            },
        )
        updated.append(material)
    return updated


def list_materials_for_workspace(workspace: Workspace) -> list[dict]:
    """Return sorted API payloads for all learning materials in a workspace."""
    sync_materials_from_storage(workspace)
    materials = list(workspace.materials.all())
    materials.sort(
        key=lambda material: (
            KIND_ORDER.index(material.kind)
            if material.kind in KIND_ORDER
            else len(KIND_ORDER),
            material.path.rsplit("/", 1)[-1].lower(),
        )
    )
    workspace_id = str(workspace.id)
    return [material.to_dict(workspace_id) for material in materials]
