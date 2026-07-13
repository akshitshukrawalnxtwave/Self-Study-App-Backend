from dataclasses import dataclass, field

from workspaces.storage.urls import workspace_file_url


@dataclass
class AgentTurnResult:
    """Outcome of one agent turn: reply text, changed files, and lesson panel."""

    assistant_text: str
    artifacts: list[dict] = field(default_factory=list)
    panel_html_url: str | None = None
    panel_lesson_path: str | None = None


def normalize_lesson_path(workspace_id: str, stored: str | None) -> str | None:
    """Return a workspace-relative lesson path from stored path or legacy URL."""
    if not stored:
        return None
    if stored.startswith("lessons/") and stored.endswith(".html"):
        return stored

    server_prefix = f"/workspaces/{workspace_id}/"
    if stored.startswith(server_prefix):
        path = stored[len(server_prefix) :]
        if path.startswith("lessons/") and path.endswith(".html"):
            return path

    from django.conf import settings

    key_prefix = getattr(settings, "AWS_S3_KEY_PREFIX", "workspaces").strip("/")
    s3_marker = f"/{key_prefix}/{workspace_id}/"
    if s3_marker in stored:
        path = stored.split(s3_marker, 1)[1].split("?", 1)[0]
        if path.startswith("lessons/") and path.endswith(".html"):
            return path

    return None


def classify_artifact(path: str) -> str:
    """Map a workspace file path to an artifact type: lesson, reference, or mission."""
    if path.startswith("lessons/") and path.endswith(".html"):
        return "lesson"
    if path.startswith("reference/") and path.endswith(".html"):
        return "reference"
    if path == "MISSION.md":
        return "mission"
    return "mission"


def build_artifacts(
    workspace_id: str,
    before: dict[str, float],
    after: dict[str, float],
) -> list[dict]:
    """Diff before/after file snapshots into created/updated artifact entries."""
    artifacts: list[dict] = []
    all_paths = set(before) | set(after)

    for path in sorted(all_paths):
        if path not in after:
            continue

        action = "created" if path not in before else "updated"
        if path in before and before[path] == after[path]:
            continue

        artifact: dict = {
            "type": classify_artifact(path),
            "path": path,
            "action": action,
        }
        if artifact["type"] in ("lesson", "reference"):
            artifact["url"] = workspace_file_url(workspace_id, path)
        artifacts.append(artifact)

    return artifacts


def latest_lesson_panel(
    workspace_id: str,
    artifacts: list[dict],
    previous_stored_path: str | None,
) -> tuple[str | None, str | None]:
    """Return (fresh html_url, relative lesson path) for the lesson panel."""
    lesson_artifacts = [
        a for a in artifacts if a.get("type") == "lesson" and a.get("path")
    ]
    if lesson_artifacts:
        path = lesson_artifacts[-1]["path"]
        return workspace_file_url(workspace_id, path), path

    previous_path = normalize_lesson_path(workspace_id, previous_stored_path)
    if previous_path:
        return workspace_file_url(workspace_id, previous_path), previous_path
    return None, None


def map_turn(
    workspace_id: str,
    assistant_text: str,
    before: dict[str, float],
    after: dict[str, float],
    previous_panel_url: str | None,
) -> AgentTurnResult:
    """Assemble the full AgentTurnResult from a turn's text and file snapshots."""
    artifacts = build_artifacts(workspace_id, before, after)
    panel_url, panel_path = latest_lesson_panel(
        workspace_id, artifacts, previous_panel_url
    )
    return AgentTurnResult(
        assistant_text=assistant_text,
        artifacts=artifacts,
        panel_html_url=panel_url,
        panel_lesson_path=panel_path,
    )
