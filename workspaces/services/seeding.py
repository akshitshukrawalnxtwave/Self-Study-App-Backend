from pathlib import Path

from django.conf import settings

from workspaces.storage import get_storage


def seed_workspace_assets(workspace_id: str) -> None:
    storage = get_storage()
    storage.ensure_workspace(workspace_id)

    seed_dir = Path(__file__).resolve().parent.parent / "seed_assets"
    for name in ("lesson.css", "quiz.js"):
        content = (seed_dir / name).read_text(encoding="utf-8")
        storage.write(workspace_id, f"assets/{name}", content)


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
