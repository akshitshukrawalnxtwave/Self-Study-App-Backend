import mimetypes
import uuid

from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from workspaces.models import ChatSession, Message, Workspace
from workspaces.services.agent import AgentError, AgentTimeoutError, agent_service
from workspaces.services.seeding import seed_workspace_assets
from workspaces.storage import get_storage
from workspaces.utils import (
    error_response,
    lesson_title_from_path,
    parse_json_body,
    turn_to_dict,
)


def _get_workspace_or_404(workspace_id: str) -> Workspace:
    try:
        return Workspace.objects.get(pk=workspace_id)
    except (Workspace.DoesNotExist, ValueError):
        raise Http404


def _get_active_session(workspace: Workspace) -> ChatSession:
    session = workspace.sessions.filter(is_active=True).first()
    if session:
        return session
    return ChatSession.objects.create(workspace=workspace, is_active=True)


@require_GET
def list_workspaces(request):
    workspaces = Workspace.objects.all()
    return JsonResponse([w.to_dict() for w in workspaces], safe=False)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def workspaces_collection(request):
    if request.method == "GET":
        return list_workspaces(request)

    body = parse_json_body(request)
    if not body:
        return error_response("Invalid JSON body", "VALIDATION_ERROR", 400)

    title = (body.get("title") or "").strip()
    topic_slug = (body.get("topic_slug") or "").strip()
    if not title or not topic_slug:
        return error_response(
            "title and topic_slug are required", "VALIDATION_ERROR", 400
        )

    existing = Workspace.objects.filter(topic_slug=topic_slug).first()
    if existing:
        return JsonResponse(existing.to_dict(), status=200)

    workspace = Workspace.objects.create(title=title, topic_slug=topic_slug)
    seed_workspace_assets(str(workspace.id))
    ChatSession.objects.create(workspace=workspace, is_active=True)

    return JsonResponse(workspace.to_dict(), status=201)


@require_GET
def list_lessons(request, workspace_id):
    workspace = _get_workspace_or_404(workspace_id)
    storage = get_storage()
    paths = storage.list(str(workspace.id), "lessons")
    html_paths = [p for p in paths if p.endswith(".html")]

    lessons = []
    for path in sorted(html_paths):
        lessons.append(
            {
                "url": f"/workspaces/{workspace.id}/{path}",
                "path": path,
                "title": lesson_title_from_path(path),
            }
        )
    return JsonResponse(lessons, safe=False)


@require_GET
def list_messages(request, workspace_id):
    workspace = _get_workspace_or_404(workspace_id)
    session = workspace.sessions.filter(is_active=True).first()
    if not session:
        return JsonResponse([], safe=False)

    messages = session.messages.all()
    return JsonResponse([m.to_dict() for m in messages], safe=False)


@csrf_exempt
@require_http_methods(["POST"])
def chat(request, workspace_id):
    workspace = _get_workspace_or_404(workspace_id)

    body = parse_json_body(request)
    if not body:
        return error_response("Invalid JSON body", "VALIDATION_ERROR", 400)

    content = (body.get("content") or "").strip()
    if not content:
        return error_response("content is required", "VALIDATION_ERROR", 400)

    session = _get_active_session(workspace)

    Message.objects.create(
        session=session,
        role=Message.ROLE_USER,
        content=content,
    )
    session.save(update_fields=["last_active_at"])

    turn_id = uuid.uuid4()
    try:
        print("Running turn", workspace)
        result = agent_service.run_turn(workspace, session, content)
    except AgentTimeoutError as exc:
        return error_response(str(exc), "AGENT_TIMEOUT", 504)
    except AgentError as exc:
        return error_response(str(exc), "INTERNAL_ERROR", 500)

    Message.objects.create(
        session=session,
        role=Message.ROLE_ASSISTANT,
        content=result.assistant_text,
        turn_id=turn_id,
    )

    if result.panel_html_url:
        workspace.last_panel_html_url = result.panel_html_url
        workspace.save(update_fields=["last_panel_html_url"])

    return JsonResponse(turn_to_dict(turn_id, result))


@require_GET
def serve_workspace_file(request, workspace_id, file_path):
    _get_workspace_or_404(workspace_id)

    if ".." in file_path.split("/"):
        return error_response("Path traversal not allowed", "FORBIDDEN", 403)

    storage = get_storage()
    try:
        if not storage.exists(workspace_id, file_path):
            raise Http404
        data = storage.read_bytes(workspace_id, file_path)
    except ValueError:
        return error_response("Path traversal not allowed", "FORBIDDEN", 403)
    except FileNotFoundError:
        raise Http404

    content_type, _ = mimetypes.guess_type(file_path)
    if content_type is None:
        content_type = "application/octet-stream"
    if content_type.startswith("text/") or content_type in (
        "application/javascript",
        "application/json",
    ):
        content_type = f"{content_type}; charset=utf-8"

    response = HttpResponse(data, content_type=content_type)
    if file_path.endswith(".html"):
        response["X-Frame-Options"] = "SAMEORIGIN"
    return response
