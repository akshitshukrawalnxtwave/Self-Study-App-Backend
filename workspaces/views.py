import logging
import hashlib
import threading
import uuid

from django.conf import settings
from django.db import close_old_connections, transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from workspaces.auth import require_authenticated_user, require_workspace_access
from workspaces.models import ChatSession, ChatTurn, Lesson, Message, Workspace
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
    validate_workspace_storage_path,
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
    """GET /api/workspaces/ — list workspaces for the current user."""
    user_id, denied = require_authenticated_user(request)
    if denied:
        return denied

    prune_orphan_workspace_dirs()
    workspaces = Workspace.objects.all()
    if user_id is not None:
        workspaces = workspaces.filter(user_id=user_id)
    return JsonResponse([w.to_dict() for w in workspaces], safe=False)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def workspaces_collection(request):
    """GET lists workspaces; POST creates one (seeded with shared assets)."""
    if request.method == "GET":
        return list_workspaces(request)

    user_id, denied = require_authenticated_user(request)
    if denied:
        return denied

    body = parse_json_body(request)
    if not body:
        return error_response("Invalid JSON body", "VALIDATION_ERROR", 400)

    title = (body.get("title") or "").strip()
    topic_slug = (body.get("topic_slug") or "").strip()
    if not title or not topic_slug:
        return error_response(
            "title and topic_slug are required", "VALIDATION_ERROR", 400
        )

    existing = Workspace.objects.filter(
        topic_slug=topic_slug, user_id=user_id
    ).first()
    if existing:
        return JsonResponse(existing.to_dict(), status=200)

    workspace = Workspace.objects.create(
        title=title, topic_slug=topic_slug, user_id=user_id
    )
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


def _workspace_version(files: list[dict]) -> str:
    """Build an opaque version that changes when workspace file metadata changes."""
    hasher = hashlib.sha256()
    for item in sorted(files, key=lambda file_info: file_info["path"]):
        hasher.update(item["path"].encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(item["etag"]).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(item["size"]).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(item["content_type"].encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


@require_GET
def workspace_manifest(request, workspace_id):
    """GET workspace file manifest for frontend S3/local cache sync."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied

    files = get_storage().manifest_files(str(workspace.id))
    return JsonResponse(
        {
            "workspace_version": _workspace_version(files),
            "files": files,
        }
    )


@csrf_exempt
@require_http_methods(["POST"])
def presign_workspace_files(request, workspace_id):
    """POST requested workspace paths and return short-lived GET URLs."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied

    body = parse_json_body(request)
    if not isinstance(body, dict):
        return error_response("Invalid JSON body", "VALIDATION_ERROR", 400)

    paths = body.get("paths")
    if not isinstance(paths, list) or not paths:
        return error_response("paths must be a non-empty list", "VALIDATION_ERROR", 400)

    invalid_paths: list[str] = []
    normalized_paths: list[str] = []
    for path in paths:
        if not isinstance(path, str):
            invalid_paths.append(str(path))
            continue
        normalized = validate_workspace_storage_path(path)
        if normalized is None:
            invalid_paths.append(path)
            continue
        normalized_paths.append(normalized)

    if invalid_paths:
        return JsonResponse(
            {
                "error": "Invalid workspace file paths",
                "code": "VALIDATION_ERROR",
                "invalid_paths": invalid_paths,
            },
            status=400,
        )

    storage = get_storage()
    workspace_id_str = str(workspace.id)
    missing_paths = [
        path for path in sorted(set(normalized_paths))
        if not storage.exists(workspace_id_str, path)
    ]
    if missing_paths:
        return JsonResponse(
            {
                "error": "Workspace file paths not found",
                "code": "NOT_FOUND",
                "missing_paths": missing_paths,
            },
            status=404,
        )

    expires_in = settings.AWS_S3_PRESIGNED_URL_EXPIRY_SECONDS
    urls = [
        {
            "path": path,
            "url": storage.presign_get_url(workspace_id_str, path, expires_in),
            "expires_in": expires_in,
        }
        for path in sorted(set(normalized_paths))
    ]
    return JsonResponse({"urls": urls})


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


def _execute_chat_turn(turn_id: uuid.UUID) -> None:
    """Run the agent for a turn in a background thread and persist the outcome."""
    close_old_connections()
    try:
        turn = ChatTurn.objects.select_related("workspace", "session").get(pk=turn_id)
        turn.status = ChatTurn.STATUS_RUNNING
        turn.save(update_fields=["status", "updated_at"])

        workspace = turn.workspace
        session = turn.session
        try:
            result = agent_service.run_turn(workspace, session, turn.user_content)
        except AgentTimeoutError as exc:
            turn.status = ChatTurn.STATUS_FAILED
            turn.error_message = str(exc)
            turn.error_code = "AGENT_TIMEOUT"
            turn.save(
                update_fields=[
                    "status",
                    "error_message",
                    "error_code",
                    "updated_at",
                ]
            )
            return
        except AgentError as exc:
            turn.status = ChatTurn.STATUS_FAILED
            turn.error_message = str(exc)
            turn.error_code = "INTERNAL_ERROR"
            turn.save(
                update_fields=[
                    "status",
                    "error_message",
                    "error_code",
                    "updated_at",
                ]
            )
            return
        except Exception as exc:
            logger.exception("Unexpected error during chat turn %s", turn_id)
            turn.status = ChatTurn.STATUS_FAILED
            turn.error_message = str(exc)
            turn.error_code = "INTERNAL_ERROR"
            turn.save(
                update_fields=[
                    "status",
                    "error_message",
                    "error_code",
                    "updated_at",
                ]
            )
            return

        Message.objects.create(
            session=session,
            role=Message.ROLE_ASSISTANT,
            content=result.assistant_text,
            turn_id=turn.id,
        )

        if result.panel_lesson_path:
            workspace.last_panel_html_url = result.panel_lesson_path
            workspace.save(update_fields=["last_panel_html_url"])

        register_lessons_from_artifacts(workspace, result.artifacts)
        register_materials_from_artifacts(workspace, result.artifacts)

        turn.result = turn_to_dict(turn.id, result)
        turn.status = ChatTurn.STATUS_COMPLETED
        turn.save(update_fields=["result", "status", "updated_at"])
    except Exception:
        logger.exception("Failed to execute chat turn %s", turn_id)
    finally:
        close_old_connections()


@csrf_exempt
@require_http_methods(["POST"])
def chat(request, workspace_id):
    """POST a user message; start an agent turn and return turn_id for polling."""
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

    turn = ChatTurn.objects.create(
        workspace=workspace,
        session=session,
        user_content=content,
        status=ChatTurn.STATUS_PENDING,
    )

    turn_id = turn.id

    def _spawn_turn():
        threading.Thread(
            target=_execute_chat_turn,
            args=(turn_id,),
            daemon=True,
            name=f"chat-turn-{turn_id}",
        ).start()

    # Start after the request transaction commits so the worker can load the row.
    transaction.on_commit(_spawn_turn)

    return JsonResponse(
        {"turn_id": str(turn_id), "status": ChatTurn.STATUS_PENDING},
        status=202,
    )


@require_GET
def get_chat_turn(request, workspace_id, turn_id):
    """GET /api/workspaces/{id}/chat/{turn_id}/ — poll turn status/result."""
    workspace = _get_workspace_or_404(workspace_id)
    if denied := _authorize_workspace(request, workspace):
        return denied

    try:
        turn = workspace.turns.get(pk=turn_id)
    except (ChatTurn.DoesNotExist, ValueError):
        raise Http404

    return JsonResponse(turn.to_status_dict())


@xframe_options_exempt
@require_GET
def serve_workspace_file(request, workspace_id, file_path):
    #this function is not getting used anywhere.
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
