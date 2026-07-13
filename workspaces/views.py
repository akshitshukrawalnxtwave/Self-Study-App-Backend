import logging
import uuid

from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from workspaces.auth import require_workspace_access
from workspaces.models import ChatSession, Lesson, Message, Workspace
from workspaces.services.agent import AgentError, AgentTimeoutError, agent_service
from workspaces.services.lessons import (
    register_lessons_from_artifacts,
    sync_lessons_from_storage,
)
from workspaces.services.materials import (
    list_materials_for_workspace,
    register_materials_from_artifacts,
)
from workspaces.services.seeding import ensure_workspace_asset, seed_workspace_assets
from workspaces.services.workspace_files import (
    apply_workspace_file_headers,
    content_type_for_file_path,
    rewrite_workspace_asset_refs,
    validate_workspace_file_path,
)
from workspaces.storage import get_storage, prune_orphan_workspace_dirs
from workspaces.utils import (
    error_response,
    parse_json_body,
    turn_to_dict,
)

logger = logging.getLogger(__name__)


def _get_workspace_or_404(workspace_id: str) -> Workspace:
    """Fetch a workspace by ID or raise 404 if it doesn't exist."""
    try:
        return Workspace.objects.get(pk=workspace_id)
    except (Workspace.DoesNotExist, ValueError):
        raise Http404


def _authorize_workspace(request, workspace: Workspace):
    """Return an error response when the caller cannot access the workspace."""
    return require_workspace_access(request, workspace)


def _get_active_session(workspace: Workspace) -> ChatSession:
    """Return the workspace's active chat session, creating one if needed."""
    session = workspace.sessions.filter(is_active=True).first()
    if session:
        return session
    return ChatSession.objects.create(workspace=workspace, is_active=True)


@require_GET
def list_workspaces(request):
    """GET /api/workspaces/ — list all workspaces as JSON."""
    prune_orphan_workspace_dirs()
    workspaces = Workspace.objects.all()
    return JsonResponse([w.to_dict() for w in workspaces], safe=False)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def workspaces_collection(request):
    """GET lists workspaces; POST creates one (seeded with shared assets)."""
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
    """GET /api/workspaces/{id}/lessons/ — list lessons (synced from storage)."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied
    sync_lessons_from_storage(workspace)
    lessons = workspace.lessons.all()
    return JsonResponse([lesson.to_list_dict() for lesson in lessons], safe=False)


@require_GET
def list_materials(request, workspace_id):
    """GET /api/workspaces/{id}/materials/ — list learning materials (synced from storage)."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied
    return JsonResponse(list_materials_for_workspace(workspace), safe=False)


@require_GET
def get_lesson(request, workspace_id, lesson_id):
    """GET lesson detail — returns { html_url } as a proxy path."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied
    try:
        lesson = workspace.lessons.get(pk=lesson_id)
    except (Lesson.DoesNotExist, ValueError):
        raise Http404

    storage = get_storage()
    if not storage.exists(str(workspace.id), lesson.path):
        raise Http404

    return JsonResponse({"html_url": lesson.html_url})


@require_GET
def list_messages(request, workspace_id):
    """GET /api/workspaces/{id}/messages/ — chat history for the active session."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied
    session = workspace.sessions.filter(is_active=True).first()
    if not session:
        return JsonResponse([], safe=False)

    messages = session.messages.all()
    return JsonResponse([m.to_dict() for m in messages], safe=False)


@csrf_exempt
@require_http_methods(["POST"])
def chat(request, workspace_id):
    """POST a user message, run an agent turn, and return the turn result."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied

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
    except Exception as exc:
        logger.exception("Unexpected error during chat turn")
        return error_response(str(exc), "INTERNAL_ERROR", 500)

    Message.objects.create(
        session=session,
        role=Message.ROLE_ASSISTANT,
        content=result.assistant_text,
        turn_id=turn_id,
    )

    if result.panel_lesson_path:
        workspace.last_panel_html_url = result.panel_lesson_path
        workspace.save(update_fields=["last_panel_html_url"])

    register_lessons_from_artifacts(workspace, result.artifacts)
    register_materials_from_artifacts(workspace, result.artifacts)

    return JsonResponse(turn_to_dict(turn_id, result))


@xframe_options_exempt
@require_GET
def serve_workspace_file(request, workspace_id, file_path):
    """Serve a workspace file from storage via the stateless file proxy."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied

    normalized_path = validate_workspace_file_path(file_path)
    if normalized_path is None:
        return error_response("Path traversal not allowed", "FORBIDDEN", 403)

    storage = get_storage()
    try:
        if not storage.exists(workspace_id, normalized_path):
            ensure_workspace_asset(workspace_id, normalized_path)
        if not storage.exists(workspace_id, normalized_path):
            raise Http404
        data = storage.read_bytes(workspace_id, normalized_path)
    except ValueError:
        return error_response("Path traversal not allowed", "FORBIDDEN", 403)
    except FileNotFoundError:
        raise Http404

    is_html = normalized_path.endswith(".html")
    if is_html:
        html = data.decode("utf-8")
        data = rewrite_workspace_asset_refs(html, workspace_id).encode("utf-8")

    response = HttpResponse(
        data, content_type=content_type_for_file_path(normalized_path)
    )
    apply_workspace_file_headers(
        response, request, is_html=is_html, file_path=normalized_path
    )
    return response
