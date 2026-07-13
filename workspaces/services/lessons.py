from workspaces.models import Lesson, Workspace
from workspaces.storage import get_storage
from workspaces.storage.urls import workspace_file_url
from workspaces.utils import lesson_title_from_path


def lesson_html_url(workspace_id: str, path: str) -> str:
    """Proxy URL the frontend should load for a lesson's HTML."""
    return workspace_file_url(workspace_id, path)


def register_lessons_from_artifacts(
    workspace: Workspace, artifacts: list[dict]
) -> list[Lesson]:
    """Create or update Lesson rows from agent turn artifacts."""
    updated: list[Lesson] = []
    for artifact in artifacts:
        if artifact.get("type") != "lesson":
            continue
        path = artifact.get("path")
        if not path:
            continue
        title = lesson_title_from_path(path)
        lesson, _ = Lesson.objects.update_or_create(
            workspace=workspace,
            path=path,
            defaults={"title": title},
        )
        updated.append(lesson)
    return updated


def sync_lessons_from_storage(workspace: Workspace) -> list[Lesson]:
    """Import lesson HTML files from storage into the DB (backfill / repair)."""
    storage = get_storage()
    paths = storage.list(str(workspace.id), "lessons")
    html_paths = sorted(p for p in paths if p.endswith(".html"))

    synced: list[Lesson] = []
    for path in html_paths:
        lesson, _ = Lesson.objects.get_or_create(
            workspace=workspace,
            path=path,
            defaults={"title": lesson_title_from_path(path)},
        )
        synced.append(lesson)
    return synced
