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
from workspaces.storage import (
    agent_cache_snapshot,
    ensure_agent_cache,
    get_storage,
    sync_agent_cache_to_remote,
    uses_agent_cache,
)

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

logger = logging.getLogger(__name__)

DEBUG_LOG_PATH = Path(settings.BASE_DIR) / ".cursor" / "debug-4a4209.log"


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict | None = None):
    """Append a structured debug entry to .cursor/debug-4a4209.log (best-effort)."""
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


def _is_agent_auth_failure(text: str) -> bool:
    """True when SDK result text is an AWS/Bedrock authentication error."""
    lower = text.lower()
    return any(
        marker in lower
        for marker in (
            "failed to authenticate",
            "not authorized to perform",
            "accessdenied",
            "api error: 403",
            "api error: 401",
        )
    )


class AgentError(Exception):
    """Raised when the agent fails to complete a turn."""


class AgentTimeoutError(AgentError):
    """Raised when an agent turn exceeds AGENT_TIMEOUT_SECONDS."""


class AgentService:
    """Runs teaching-agent turns and syncs workspace files around them."""

    def run_turn(
        self,
        workspace: Workspace,
        session: ChatSession,
        user_content: str,
    ) -> AgentTurnResult:
        """Run one chat turn: hydrate cache, invoke agent, sync files, map artifacts."""
        storage = get_storage()
        workspace_id = str(workspace.id)
        use_cache = uses_agent_cache() and not settings.AGENT_FIXTURE_MODE

        # Agent SDK needs a real filesystem cwd. Hydrate workspaces_data/ when needed.
        if use_cache:
            ensure_agent_cache(workspace_id)
            before = agent_cache_snapshot(workspace_id)
        else:
            before = storage.snapshot(workspace_id)
        # #region agent log
        _debug_log(
            "H1",
            "agent.py:run_turn:entry",
            "run_turn started",
            {
                "fixture_mode": settings.AGENT_FIXTURE_MODE,
                "workspace_id": workspace_id,
                "storage_backend": settings.STORAGE_BACKEND,
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

        if use_cache:
            after = agent_cache_snapshot(workspace_id)
            uploaded = sync_agent_cache_to_remote(workspace_id)
            _debug_log(
                "H4",
                "agent.py:run_turn:remote_sync",
                "synced agent cache to remote storage",
                {"uploaded_count": len(uploaded), "uploaded": uploaded[:20]},
            )
        else:
            after = storage.snapshot(workspace_id)
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
        """Run the SDK turn in a worker thread, enforcing the configured timeout."""
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
        """Canned turn for AGENT_FIXTURE_MODE (dev/testing without the SDK)."""
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
        """Local filesystem path used as the agent's working directory (cwd)."""
        return str(Path(settings.WORKSPACES_ROOT) / str(workspace.id))

    def _build_sdk_prompt(self, user_content: str) -> str:
        """Wrap the user message in the /teach skill command."""
        return f"/teach {user_content}"

    def _build_sdk_options(self, workspace: Workspace, session: ChatSession):
        """Build ClaudeAgentOptions (cwd, tools, permissions, session resume)."""
        

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
            "model": settings.AGENT_MODEL,
        }

        if settings.AGENT_SDK_ENV:
            options_kwargs["env"] = dict(settings.AGENT_SDK_ENV)
        if session.sdk_session_id:
            options_kwargs["resume"] = session.sdk_session_id
        return ClaudeAgentOptions(**options_kwargs)

    def _extract_assistant_text(self, messages: list) -> str:
        """Pull the final assistant reply out of collected SDK messages."""
        assistant_parts: list[str] = []
        result_text: str | None = None
        result_error: str | None = None

        for message in messages:
            if isinstance(message, ResultMessage):
                if message.result:
                    result_text = message.result
                if message.is_error:
                    result_error = (
                        message.result
                        or (message.errors[0] if message.errors else None)
                        or message.subtype
                    )
                    if message.api_error_status:
                        logger.warning(
                            "Agent result flagged error api_status=%s subtype=%s errors=%s",
                            message.api_error_status,
                            message.subtype,
                            message.errors,
                        )
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        assistant_parts.append(block.text)

        if result_text and result_text.strip():
            if result_error and _is_agent_auth_failure(result_text):
                raise AgentError(
                    "AWS agent authentication failed. For Claude Platform on AWS, "
                    "set AGENT_PROVIDER=anthropic_aws and ANTHROPIC_AWS_WORKSPACE_ID. "
                    "For classic Bedrock, add bedrock:InvokeModel IAM permissions."
                )
            if result_error:
                logger.warning(
                    "Using result text despite SDK error flag: %s", result_error
                )
            return result_text
        if assistant_parts:
            if result_error:
                logger.warning(
                    "Using assistant messages despite SDK error flag: %s",
                    result_error,
                )
            return "\n".join(assistant_parts)
        if result_error and result_error not in ("success", "unknown error"):
            raise AgentError(result_error)
        return ""

    def _persist_sdk_session_id(
        self, session: ChatSession, sdk_session_id: str | None
    ) -> None:
        """Store the SDK session ID on the chat session for future resume."""
        if sdk_session_id and session.sdk_session_id != sdk_session_id:
            session.sdk_session_id = sdk_session_id
            session.save(update_fields=["sdk_session_id"])

    def _sdk_turn(
        self, workspace: Workspace, session: ChatSession, user_content: str
    ):
        """Execute one turn against the Claude Agent SDK and return its text."""
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
            "SDK turn workspace=%s provider=%s effective=%s model=%s resume=%s",
            workspace.id,
            settings.AGENT_PROVIDER,
            getattr(settings, 'AGENT_EFFECTIVE_PROVIDER', settings.AGENT_PROVIDER),
            settings.AGENT_MODEL,
            bool(session.sdk_session_id),
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
                {"error": str(exc), "collected_count": len(collected_messages)},
            )
            # #endregion
            if not collected_messages:
                raise AgentError(str(exc)) from exc
            logger.warning(
                "SDK process error after collecting %d messages: %s",
                len(collected_messages),
                exc,
            )
        except Exception as exc:
            # SDK may raise after a result with is_error=True when the CLI exits
            # (common with Bedrock). Recover if we already have messages.
            if not collected_messages:
                raise AgentError(str(exc)) from exc
            logger.warning(
                "SDK stream error after collecting %d messages: %s",
                len(collected_messages),
                exc,
            )

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
            raise AgentError(
                "Agent completed without a response. "
                "Check Bedrock model access and IAM permissions."
            )

        return {"text": assistant_text}


agent_service = AgentService()
