from dataclasses import dataclass, field


@dataclass
class AgentTurnResult:
    assistant_text: str
    artifacts: list[dict] = field(default_factory=list)
    panel_html_url: str | None = None


def workspace_file_url(workspace_id: str, path: str) -> str:
    return f"/workspaces/{workspace_id}/{path}"


def classify_artifact(path: str) -> str:
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


def latest_lesson_url(
    workspace_id: str,
    artifacts: list[dict],
    previous_url: str | None,
) -> str | None:
    lesson_artifacts = [
        a for a in artifacts if a.get("type") == "lesson" and a.get("url")
    ]
    if lesson_artifacts:
        return lesson_artifacts[-1]["url"]
    return previous_url


def map_turn(
    workspace_id: str,
    assistant_text: str,
    before: dict[str, float],
    after: dict[str, float],
    previous_panel_url: str | None,
) -> AgentTurnResult:
    artifacts = build_artifacts(workspace_id, before, after)
    panel_url = latest_lesson_url(workspace_id, artifacts, previous_panel_url)
    return AgentTurnResult(
        assistant_text=assistant_text,
        artifacts=artifacts,
        panel_html_url=panel_url,
    )
