import json
import re

from django.http import JsonResponse


def parse_json_body(request) -> dict | None:
    if not request.body:
        return None
    try:
        return json.loads(request.body)
    except json.JSONDecodeError:
        return None


def error_response(message: str, code: str, status: int) -> JsonResponse:
    return JsonResponse({"error": message, "code": code}, status=status)


def lesson_title_from_path(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    name = re.sub(r"^\d+-", "", name)
    name = re.sub(r"\.html$", "", name)
    return name.replace("-", " ").title()


def turn_to_dict(turn_id, result) -> dict:
    return {
        "turn_id": str(turn_id),
        "messages": [
            {
                "role": "assistant",
                "type": "text",
                "content": result.assistant_text,
            }
        ],
        "artifacts": result.artifacts,
        "panel": {"html_url": result.panel_html_url},
    }
