from pathlib import Path

from django.conf import settings

from workspaces.storage import get_storage

SEED_ASSET_NAMES = ("lesson.css", "quiz.js")
PROTECTED_SEED_ASSET_PATHS = frozenset(f"assets/{name}" for name in SEED_ASSET_NAMES)


def _sanitize_seed_content(name: str, content: str) -> str:
    """Strip accidental Python string wrappers the agent may have written."""
    stripped = content.strip()
    if stripped.startswith('"""') and stripped.endswith('"'):
        stripped = stripped[3:-1].strip()
    elif stripped.startswith('"""') and stripped.endswith('"""'):
        stripped = stripped[3:-3].strip()
    elif stripped.startswith('"') and stripped.endswith('"') and name.endswith(".css"):
        stripped = stripped[1:-1].strip()
    return stripped


def seed_workspace_assets(workspace_id: str) -> None:
    """Write the shared lesson.css and quiz.js into a workspace's assets/ folder."""
    storage = get_storage()
    seed_dir = Path(__file__).resolve().parent.parent / "seed_assets"
    for name in SEED_ASSET_NAMES:
        content = _sanitize_seed_content(
            name, (seed_dir / name).read_text(encoding="utf-8")
        )
        storage.write(workspace_id, f"assets/{name}", content)


def ensure_workspace_asset(workspace_id: str, path: str) -> bool:
    """Backfill missing seed assets (e.g. workspace created before S3 upload)."""
    if not path.startswith("assets/"):
        return False
    name = path.removeprefix("assets/")
    if name not in SEED_ASSET_NAMES:
        return False

    storage = get_storage()
    if storage.exists(workspace_id, path):
        return True

    seed_dir = Path(__file__).resolve().parent.parent / "seed_assets"
    seed_file = seed_dir / name
    if not seed_file.is_file():
        return False

    storage.write(
        workspace_id,
        path,
        _sanitize_seed_content(name, seed_file.read_text(encoding="utf-8")),
    )
    return True


SAMPLE_LESSON_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <link rel="stylesheet" href="../assets/lesson.css">
</head>
<body>
  <h1>{title}</h1>
  <p class="lesson-meta">Lesson 1 — Getting started</p>
  <p>{intro}</p>
  <div class="quiz" data-quiz data-answer="pressure">
    <p><strong>Quick check:</strong> What increases with depth in a fluid at rest?</p>
    <input type="text" placeholder="Your answer">
    <button>Check</button>
    <p class="feedback"></p>
  </div>
  <script src="../assets/quiz.js"></script>
</body>
</html>
"""
