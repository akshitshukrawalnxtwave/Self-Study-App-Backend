"""Derive student-facing status messages from Claude Agent SDK stream events."""

from __future__ import annotations

from typing import Any


DEFAULT_RUNNING = "Working on your request…"
THINKING = "Thinking…"
COMPOSING = "Preparing a response…"
SEARCHING = "Searching your materials…"
RUNNING_COMMAND = "Getting things ready…"


def _path_from_tool_input(tool_input: dict[str, Any] | None) -> str:
    """Best-effort file path from a tool input dict."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("file_path", "path", "filename", "target_file"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().replace("\\", "/")
    return ""


def _message_for_path(action: str, path: str) -> str:
    """Map a workspace path to a friendly activity label."""
    lower = path.lower()
    name = path.rsplit("/", 1)[-1].lower()

    if name == "mission.md" or lower.endswith("/mission.md"):
        return "Reviewing your goals…" if action == "read" else "Updating your goals…"
    if name == "resources.md" or lower.endswith("/resources.md"):
        return "Checking resources…" if action == "read" else "Updating resources…"
    if name == "notes.md" or lower.endswith("/notes.md"):
        return "Checking your notes…" if action == "read" else "Updating your notes…"
    if "/lessons/" in f"/{lower}" or lower.startswith("lessons/"):
        return "Reviewing a lesson…" if action == "read" else "Writing a lesson…"
    if "/learning-records/" in f"/{lower}" or lower.startswith("learning-records/"):
        return (
            "Looking at what you've learned…"
            if action == "read"
            else "Saving a learning note…"
        )
    if "/reference/" in f"/{lower}" or lower.startswith("reference/"):
        return "Looking up a reference…" if action == "read" else "Updating a reference…"
    if "/assets/" in f"/{lower}" or lower.startswith("assets/"):
        return (
            "Checking lesson styles…"
            if action == "read"
            else "Updating shared lesson assets…"
        )

    if action == "read":
        return "Reading your materials…"
    return "Updating your workspace…"


def message_for_tool(name: str, tool_input: dict[str, Any] | None = None) -> str:
    """Return a friendly status string for a tool invocation."""
    tool = (name or "").strip()
    path = _path_from_tool_input(tool_input)

    if tool == "Read":
        return _message_for_path("read", path) if path else "Reading your materials…"
    if tool in ("Write", "Edit"):
        return _message_for_path("write", path) if path else "Updating your workspace…"
    if tool in ("Glob", "Grep"):
        return SEARCHING
    if tool == "Bash":
        return RUNNING_COMMAND
    if tool in ("Agent", "Task"):
        return "Exploring a related idea…"
    return DEFAULT_RUNNING


def status_message_from_sdk_message(message: Any) -> str | None:
    """
    Extract a progress label from one SDK message, or None if nothing useful.

    Prefers tool activity over thinking/text so polls show concrete work.
    """
    try:
        from claude_agent_sdk.types import (
            AssistantMessage,
            TextBlock,
            ThinkingBlock,
            ToolUseBlock,
        )
    except ImportError:
        AssistantMessage = TextBlock = ThinkingBlock = ToolUseBlock = ()  # type: ignore

    if AssistantMessage and isinstance(message, AssistantMessage):
        tool_message: str | None = None
        saw_thinking = False
        saw_text = False
        for block in message.content or []:
            if isinstance(block, ToolUseBlock):
                tool_message = message_for_tool(block.name, block.input)
            elif isinstance(block, ThinkingBlock):
                saw_thinking = True
            elif isinstance(block, TextBlock) and block.text.strip():
                saw_text = True
        if tool_message:
            return tool_message
        if saw_thinking:
            return THINKING
        if saw_text:
            return COMPOSING
        return None

    # Fallback when SDK types are unavailable: duck-type content blocks.
    content = getattr(message, "content", None)
    if isinstance(content, list):
        tool_message = None
        saw_thinking = False
        saw_text = False
        for block in content:
            name = getattr(block, "name", None)
            tool_input = getattr(block, "input", None)
            if isinstance(name, str) and isinstance(tool_input, dict):
                tool_message = message_for_tool(name, tool_input)
            elif getattr(block, "thinking", None) is not None:
                saw_thinking = True
            elif isinstance(getattr(block, "text", None), str) and block.text.strip():
                saw_text = True
        if tool_message:
            return tool_message
        if saw_thinking:
            return THINKING
        if saw_text:
            return COMPOSING

    last_tool = getattr(message, "last_tool_name", None)
    if isinstance(last_tool, str) and last_tool.strip():
        return message_for_tool(last_tool)

    description = getattr(message, "description", None)
    if isinstance(description, str) and description.strip():
        text = " ".join(description.split())
        if len(text) > 120:
            text = text[:117].rstrip() + "…"
        return text

    subtype = getattr(message, "subtype", None)
    if subtype == "task_started":
        return "Starting a deeper look…"
    if subtype == "task_progress":
        return DEFAULT_RUNNING

    return None
