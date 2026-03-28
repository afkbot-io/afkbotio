"""Activity snapshot helpers for interactive chat session state."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.agent_loop.progress_stream import CanonicalProgressStage, ProgressEvent
from afkbot.services.chat_session.text_utils import truncate_compact_text

_ACTIVITY_DETAIL_PARAM_KEYS: tuple[str, ...] = (
    "cmd",
    "path",
    "query",
    "url",
    "name",
)


@dataclass(frozen=True, slots=True)
class ChatActivitySnapshot:
    """Last visible activity observed for one interactive chat session."""

    stage: CanonicalProgressStage
    summary: str
    detail: str | None = None
    running: bool = True


def starting_chat_activity() -> ChatActivitySnapshot:
    """Return the deterministic activity snapshot used when a new turn starts."""

    return ChatActivitySnapshot(stage="thinking", summary="starting", running=True)


def capture_chat_activity(event: ProgressEvent) -> ChatActivitySnapshot | None:
    """Build one concise activity snapshot from a progress event."""

    summary = _activity_summary(event)
    if summary is None:
        return None
    return ChatActivitySnapshot(
        stage=event.stage,
        summary=summary,
        detail=_activity_detail(event),
        running=_is_running_activity(event),
    )


def _activity_summary(event: ProgressEvent) -> str | None:
    if event.stage == "thinking":
        return "thinking"
    if event.stage == "planning":
        return "planning"
    if event.stage == "tool_call":
        tool_name = event.tool_name or "tool"
        if event.event_type == "tool.result" and not _is_live_tool_result(event):
            return f"tool done: {tool_name}"
        return f"tool: {tool_name}"
    if event.stage == "subagent_wait":
        tool_name = event.tool_name or "subagent"
        if event.event_type == "tool.result":
            return f"subagent done: {tool_name}"
        return f"subagent: {tool_name}"
    if event.stage == "done":
        return "response ready"
    if event.stage == "cancelled":
        return "cancelled"
    return None


def _activity_detail(event: ProgressEvent) -> str | None:
    if event.stage in {"thinking", "planning"}:
        if event.iteration is None or event.iteration <= 0:
            return None
        return f"iteration {event.iteration}"
    if event.stage == "tool_call":
        return _tool_activity_detail(event)
    if event.stage == "subagent_wait":
        if event.event_type == "tool.result":
            return None
        tool_name = str(event.tool_name or "").strip()
        return tool_name or None
    return None


def _tool_activity_detail(event: ProgressEvent) -> str | None:
    if event.event_type == "tool.call":
        params = event.tool_call_params or {}
        for key in _ACTIVITY_DETAIL_PARAM_KEYS:
            raw_value = params.get(key)
            if raw_value is None:
                continue
            text = str(raw_value).strip()
            if text:
                return f"{key}={truncate_compact_text(text, max_length=60)}"
        return None
    if event.event_type == "tool.progress":
        return _last_preview_line(event)
    if event.event_type == "tool.result":
        if _is_live_tool_result(event):
            return _live_tool_result_detail(event)
        return _tool_result_detail(event)
    return None


def _live_tool_result_detail(event: ProgressEvent) -> str | None:
    result = event.tool_result or {}
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return None
    stdout = str(payload.get("stdout") or "").strip()
    if stdout:
        return truncate_compact_text(stdout, max_length=60)
    return None


def _tool_result_detail(event: ProgressEvent) -> str | None:
    result = event.tool_result or {}
    if result.get("ok") is False:
        reason = str(result.get("reason") or "").strip()
        if reason:
            return truncate_compact_text(reason, max_length=60)
        error_code = str(result.get("error_code") or "").strip()
        return error_code or None
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return None
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, int):
        return f"exit_code={exit_code}"
    return None


def _last_preview_line(event: ProgressEvent) -> str | None:
    progress = event.tool_progress or {}
    preview_lines = progress.get("preview_lines")
    if not isinstance(preview_lines, list):
        return None
    for raw_line in reversed(preview_lines):
        line = str(raw_line).strip()
        if line:
            return truncate_compact_text(line, max_length=60)
    return None


def _is_live_tool_result(event: ProgressEvent) -> bool:
    if event.event_type != "tool.result":
        return False
    result = event.tool_result or {}
    payload = result.get("payload")
    return isinstance(payload, dict) and payload.get("running") is True


def _is_running_activity(event: ProgressEvent) -> bool:
    if event.stage in {"done", "cancelled"}:
        return False
    if event.stage == "tool_call":
        return event.event_type != "tool.result" or _is_live_tool_result(event)
    if event.stage == "subagent_wait":
        return event.event_type in {"tool.call", "tool.progress"}
    return True
