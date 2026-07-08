import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from pathlib import Path

from django.conf import settings

from workspaces.models import ChatSession, Message, Workspace
from workspaces.services.response_mapper import AgentTurnResult, map_turn
from workspaces.services.seeding import SAMPLE_LESSON_HTML
from workspaces.storage import get_storage

logger = logging.getLogger(__name__)

DEBUG_LOG_PATH = Path(settings.BASE_DIR) / ".cursor" / "debug-4a4209.log"


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict | None = None):
    # #region agent log
    try:
        payload = {
            "sessionId": "4a4209",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception:
        pass
    # #endregion

APP_SYSTEM_PROMPT = (
    "You are the teaching backend for a self-study app. "
    "The user sees chat on the left and a lesson pane on the right."
)


class AgentError(Exception):
    pass


class AgentTimeoutError(AgentError):
    pass


class AgentService:
    def run_turn(
        self,
        workspace: Workspace,
        session: ChatSession,
        user_content: str,
    ) -> AgentTurnResult:
        storage = get_storage()
        before = storage.snapshot(str(workspace.id))
        # #region agent log
        _debug_log(
            "H1",
            "agent.py:run_turn:entry",
            "run_turn started",
            {
                "fixture_mode": settings.AGENT_FIXTURE_MODE,
                "workspace_id": str(workspace.id),
                "permission_mode": settings.AGENT_PERMISSION_MODE,
                "before_files": sorted(before.keys()),
                "before_count": len(before),
            },
        )
        # #endregion

        if settings.AGENT_FIXTURE_MODE:
            result = self._fixture_turn(workspace, session, user_content, storage)
        else:
            result = self._run_with_timeout(workspace, session, user_content)

        after = storage.snapshot(str(workspace.id))
        turn_result = map_turn(
            str(workspace.id),
            result["text"],
            before,
            after,
            workspace.last_panel_html_url or None,
        )
        # #region agent log
        new_files = [p for p in after if p not in before]
        changed_files = [
            p for p in after if p in before and after[p] != before[p]
        ]
        _debug_log(
            "H4",
            "agent.py:run_turn:exit",
            "run_turn completed",
            {
                "workspace_id": str(workspace.id),
                "after_files": sorted(after.keys()),
                "new_files": new_files,
                "changed_files": changed_files,
                "artifacts": turn_result.artifacts,
                "panel_html_url": turn_result.panel_html_url,
                "assistant_text_len": len(turn_result.assistant_text),
            },
        )
        # #endregion
        return turn_result

    def _run_with_timeout(
        self, workspace: Workspace, session: ChatSession, user_content: str
    ):
        timeout = settings.AGENT_TIMEOUT_SECONDS
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                self._sdk_turn, workspace, session, user_content
            )
            try:
                return future.result(timeout=timeout)
            except FuturesTimeoutError as exc:
                raise AgentTimeoutError(
                    f"Agent timed out after {timeout}s"
                ) from exc

    def _fixture_turn(self, workspace, session, user_content, storage):
        user_count = session.messages.filter(role=Message.ROLE_USER).count()

        if user_count <= 1:
            return {
                "text": (
                    "Welcome! Why do you want to learn this topic, "
                    "and what do you hope to achieve?"
                ),
            }

        slug = workspace.topic_slug.replace("-", " ").title()
        lesson_path = "lessons/0001-getting-started.html"
        if not storage.exists(str(workspace.id), lesson_path):
            storage.write(
                str(workspace.id),
                lesson_path,
                SAMPLE_LESSON_HTML.format(
                    title=f"Introduction to {slug}",
                    intro=(
                        f"This is your first lesson on {slug}. "
                        "Read through the material and ask me anything in the chat."
                    ),
                ),
            )
            storage.write(
                str(workspace.id),
                "MISSION.md",
                f"# Mission\n\nLearn {slug} through guided lessons.\n",
            )
            return {
                "text": (
                    f"I've created your first lesson on {slug}. "
                    "Read it on the right and ask me anything."
                ),
            }

        return {
            "text": (
                "Great question! Keep exploring the lesson on the right. "
                "I'm here to clarify concepts whenever you need."
            ),
        }

    def _workspace_path(self, workspace: Workspace) -> str:
        return str(Path(settings.WORKSPACES_ROOT) / str(workspace.id))

    def _build_sdk_prompt(self, user_content: str) -> str:
        return f"/teach {user_content}"

    def _build_sdk_options(self, workspace: Workspace, session: ChatSession):
        from claude_agent_sdk import ClaudeAgentOptions

        workspace_path = self._workspace_path(workspace)
        skill_path = Path(settings.BASE_DIR) / ".claude" / "skills" / "teach" / "SKILL.md"
        wp = Path(workspace_path)
        # #region agent log
        _debug_log(
            "H2",
            "agent.py:_build_sdk_options",
            "sdk options context",
            {
                "cwd": workspace_path,
                "workspace_exists": wp.exists(),
                "lessons_dir_exists": (wp / "lessons").exists(),
                "skill_md_exists": skill_path.exists(),
                "skill_md_path": str(skill_path),
                "base_dir": str(settings.BASE_DIR),
                "resume": session.sdk_session_id or None,
            },
        )
        # #endregion

        options_kwargs = {
            "cwd": workspace_path,
            "skills": ["teach"],
            "allowed_tools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash"],
            "permission_mode": settings.AGENT_PERMISSION_MODE,
            "system_prompt": APP_SYSTEM_PROMPT,
            "max_turns": settings.AGENT_MAX_TURNS,
        }
        if session.sdk_session_id:
            options_kwargs["resume"] = session.sdk_session_id
        return ClaudeAgentOptions(**options_kwargs)

    def _extract_assistant_text(self, messages: list) -> str:
        from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

        assistant_parts: list[str] = []
        result_text: str | None = None

        for message in messages:
            if isinstance(message, ResultMessage):
                if message.session_id:
                    pass  # handled by caller
                if message.is_error:
                    error_detail = (
                        message.result
                        or (message.errors[0] if message.errors else None)
                        or "Agent run failed"
                    )
                    raise AgentError(error_detail)
                if message.result:
                    result_text = message.result
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        assistant_parts.append(block.text)

        if result_text:
            return result_text
        return "\n".join(assistant_parts)

    def _persist_sdk_session_id(
        self, session: ChatSession, sdk_session_id: str | None
    ) -> None:
        if sdk_session_id and session.sdk_session_id != sdk_session_id:
            session.sdk_session_id = sdk_session_id
            session.save(update_fields=["sdk_session_id"])

    def _sdk_turn(
        self, workspace: Workspace, session: ChatSession, user_content: str
    ):
        try:
            from claude_agent_sdk import query
            from claude_agent_sdk._errors import ProcessError
        except ImportError as exc:
            raise AgentError(
                "Claude Agent SDK not installed. Set AGENT_FIXTURE_MODE=true."
            ) from exc

        prompt = self._build_sdk_prompt(user_content)
        options = self._build_sdk_options(workspace, session)
        logger.info(
            "SDK turn workspace=%s resume=%s permission=%s",
            workspace.id,
            bool(session.sdk_session_id),
            settings.AGENT_PERMISSION_MODE,
        )
        collected_messages: list = []
        sdk_session_id: str | None = session.sdk_session_id or None

        async def _collect():
            nonlocal sdk_session_id
            async for message in query(prompt=prompt, options=options):
                collected_messages.append(message)
                message_session_id = getattr(message, "session_id", None)
                if message_session_id:
                    sdk_session_id = message_session_id

        try:
            asyncio.run(_collect())
        except ProcessError as exc:
            # #region agent log
            _debug_log(
                "H4",
                "agent.py:_sdk_turn:process_error",
                "sdk process error",
                {"error": str(exc)},
            )
            # #endregion
            raise AgentError(str(exc)) from exc

        message_types = {}
        for msg in collected_messages:
            name = type(msg).__name__
            message_types[name] = message_types.get(name, 0) + 1
        # #region agent log
        _debug_log(
            "H4",
            "agent.py:_sdk_turn:messages",
            "sdk message summary",
            {
                "message_types": message_types,
                "message_count": len(collected_messages),
                "sdk_session_id": sdk_session_id,
            },
        )
        # #endregion

        self._persist_sdk_session_id(session, sdk_session_id)

        assistant_text = self._extract_assistant_text(collected_messages)
        if not assistant_text.strip():
            assistant_text = (
                "I'm ready to help you learn. What would you like to explore?"
            )

        return {"text": assistant_text}


agent_service = AgentService()
