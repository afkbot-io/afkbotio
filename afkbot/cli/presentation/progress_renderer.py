"""Text renderer for CLI progress statuses."""

from __future__ import annotations

import json
from typing import assert_never

from afkbot.cli.presentation.terminal_text import sanitize_terminal_text
from afkbot.cli.presentation.progress_mapper import RenderEvent
from afkbot.services.agent_loop.progress_stream import ProgressEvent

_HIDDEN_PARAM_KEYS = frozenset(
    {
        "profile_id",
        "profile_key",
        "timeout_sec",
        "skill_name",
        "credential_profile_key",
    }
)
_PRIORITY_PARAM_KEYS: tuple[str, ...] = (
    "cmd",
    "command",
    "args",
    "cwd",
    "path",
    "query",
    "glob",
    "search",
    "replace",
    "url",
    "chat_id",
    "to_email",
    "subject",
    "name",
)
_ANSI_BLUE = "\033[94m"
_ANSI_VIOLET = "\033[95m"
_ANSI_WARNING = "\033[93m"
_ANSI_SUCCESS = "\033[92m"
_ANSI_ERROR = "\033[91m"


def render_progress_event(event: RenderEvent) -> str:
    """Render one progress event as short CLI status text."""

    tool_name = sanitize_terminal_text(event.tool_name) if event.tool_name else None
    marker = _status_marker(event)
    if event.event_type == "llm.call.compaction_start":
        return "----- Automatic context compaction -----"
    if event.event_type == "llm.call.compaction_done":
        return "----- Context automatically compacted -----"
    if event.event_type == "llm.call.compaction_failed":
        return "----- Automatic context compaction failed -----"

    match event.stage:
        case "thinking":
            return "thinking..."
        case "planning":
            return "planning..."
        case "tool_call":
            if event.event_type == "tool.progress" or (
                event.event_type == "tool.result" and event.live_result
            ):
                if tool_name:
                    return f"{marker}tool running: {tool_name}"
                return f"{marker}tool running".strip()
            if event.event_type == "tool.result":
                if tool_name:
                    return f"{marker}tool completed: {tool_name}"
                return f"{marker}tool completed".strip()
            if tool_name:
                return f"{marker}calling tool: {tool_name}"
            return f"{marker}calling tool".strip()
        case "subagent_wait":
            if event.event_type == "tool.result":
                if tool_name:
                    return f"{marker}subagent completed: {tool_name}"
                return f"{marker}subagent completed".strip()
            if tool_name:
                return f"{marker}waiting subagent: {tool_name}"
            return f"{marker}waiting subagent".strip()
        case "done":
            return "response ready"
        case "cancelled":
            return "cancelled"
        case other:
            assert_never(other)


def render_progress_color(event: RenderEvent, *, progress_event: ProgressEvent | None = None) -> str:
    """Return ANSI color escape for one progress status line."""

    if event.event_type == "llm.call.compaction_done":
        return _ANSI_SUCCESS
    if event.event_type == "llm.call.compaction_failed":
        return _ANSI_ERROR
    if event.event_type == "llm.call.compaction_start":
        return _ANSI_WARNING
    if event.stage == "done":
        return _ANSI_SUCCESS
    if event.stage == "cancelled":
        return _ANSI_ERROR
    if event.stage in {"tool_call", "subagent_wait"}:
        if event.event_type == "tool.result" and not event.live_result:
            return _ANSI_ERROR if _is_tool_result_error(progress_event) else _ANSI_SUCCESS
        return _ANSI_WARNING
    if event.stage == "planning":
        return _ANSI_VIOLET
    if event.stage == "thinking" and event.iteration is not None and event.iteration > 0:
        return _ANSI_WARNING
    return _ANSI_BLUE


def render_progress_detail(event: ProgressEvent) -> str | None:
    """Render one gray detail line for tool/subagent progress when available."""

    if event.event_type.startswith("llm.call.compaction_"):
        return _render_compaction_details(event)
    if event.event_type.startswith("llm.call."):
        return _render_llm_call_details(event)
    if event.event_type == "turn.plan":
        return _render_turn_plan_details(event)
    if event.event_type == "tool.progress":
        lines = render_progress_detail_lines(event)
        return None if not lines else lines[-1]
    if event.stage not in {"tool_call", "subagent_wait"}:
        return None
    if event.event_type == "tool.call":
        return _render_tool_call_details(event)
    if event.event_type == "tool.result":
        return _render_tool_result_details(event)
    return None


def render_progress_detail_lines(event: ProgressEvent) -> tuple[str, ...]:
    """Render one or more detail lines for richer interactive tool progress blocks."""

    if event.event_type == "tool.progress":
        progress = event.tool_progress or {}
        preview_lines = progress.get("preview_lines")
        if not isinstance(preview_lines, list):
            return ()
        lines = tuple(
            sanitize_terminal_text(str(item).strip())
            for item in preview_lines
            if str(item).strip()
        )
        return tuple(line for line in lines if line)

    detail = render_progress_detail(event)
    return () if detail is None else (detail,)


def _render_tool_call_details(event: ProgressEvent) -> str | None:
    params = event.tool_call_params or {}
    if not params:
        return None
    if event.tool_name == "bash.exec":
        raw_cmd = params.get("cmd")
        raw_cwd = params.get("cwd")
        raw_session_id = params.get("session_id")
        raw_chars = params.get("chars")
        cmd = None if raw_cmd is None else _fmt_value(raw_cmd)
        cwd = None if raw_cwd is None else _fmt_value(raw_cwd)
        session_id = None if raw_session_id is None else _fmt_value(raw_session_id)
        chars = None if raw_chars in (None, "") else _fmt_value(raw_chars)
        bash_parts: list[str] = []
        if cmd:
            bash_parts.append(f"cmd={cmd}")
        if cwd:
            bash_parts.append(f"cwd={cwd}")
        if session_id:
            bash_parts.append(f"session_id={session_id}")
        if chars:
            bash_parts.append(f"chars={chars}")
        if bash_parts:
            return "params: " + " ".join(bash_parts)
        return None

    ordered_keys: list[str] = []
    for key in _PRIORITY_PARAM_KEYS:
        if key in params and key not in _HIDDEN_PARAM_KEYS:
            ordered_keys.append(key)
    for key in sorted(params.keys()):
        if key in _HIDDEN_PARAM_KEYS or key in ordered_keys:
            continue
        ordered_keys.append(key)
    parts: list[str] = []
    for key in ordered_keys[:8]:
        parts.append(f"{key}={_fmt_value(params.get(key))}")
    if not parts:
        return None
    return "params: " + " ".join(parts)


def _render_tool_result_details(event: ProgressEvent) -> str | None:
    result = event.tool_result or {}
    if not result:
        return None
    ok = result.get("ok")
    if ok is False:
        error_code = str(result.get("error_code") or "").strip() or "tool_error"
        reason = str(result.get("reason") or "").strip()
        if reason:
            return f"error={error_code} reason={_fmt_value(reason)}"
        return f"error={error_code}"

    payload = result.get("payload")
    if not isinstance(payload, dict):
        return "ok"

    if event.tool_name == "bash.exec":
        exit_code = payload.get("exit_code")
        session_id = str(payload.get("session_id") or "").strip()
        running = payload.get("running") is True
        stdout = str(payload.get("stdout") or "").strip()
        stderr = str(payload.get("stderr") or "").strip()
        bash_parts: list[str] = []
        if session_id:
            bash_parts.append(f"session_id={_fmt_value(session_id)}")
        if running:
            bash_parts.append("running=true")
        if exit_code is not None:
            bash_parts.append(f"exit_code={exit_code!s}")
        if stdout:
            bash_parts.append(f"stdout={_fmt_value(stdout)}")
        if stderr:
            bash_parts.append(f"stderr={_fmt_value(stderr)}")
        if not bash_parts:
            return "ok"
        return " ".join(bash_parts)

    if "count" in payload:
        return f"count={payload.get('count')!s}"
    if "status_code" in payload:
        return f"status_code={payload.get('status_code')!s}"
    if "sent" in payload:
        return f"sent={payload.get('sent')!s}"
    if "message_id" in payload:
        return f"message_id={payload.get('message_id')!s}"
    return "ok"


def _render_llm_call_details(event: ProgressEvent) -> str | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    elapsed_ms = payload.get("elapsed_ms")
    timeout_ms = payload.get("timeout_ms")
    response_kind = str(payload.get("response_kind") or "").strip()
    error_code = str(payload.get("error_code") or "").strip()
    reasoning_effort = str(payload.get("reasoning_effort") or "").strip()
    available_tool_names = payload.get("available_tool_names")
    status = event.event_type.removeprefix("llm.call.").strip()

    parts: list[str] = []
    if status:
        parts.append(f"llm={status}")
    if isinstance(elapsed_ms, int):
        parts.append(f"elapsed_ms={elapsed_ms}")
    if isinstance(timeout_ms, int):
        parts.append(f"timeout_ms={timeout_ms}")
    if reasoning_effort:
        parts.append(f"reasoning={reasoning_effort}")
    if isinstance(available_tool_names, list):
        parts.append(f"visible_tools={len(available_tool_names)}")
    if response_kind:
        parts.append(f"kind={response_kind}")
    if error_code:
        parts.append(f"error={error_code}")
    if not parts:
        return None
    return " ".join(parts)


def _render_compaction_details(event: ProgressEvent) -> str | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    attempt = payload.get("attempt")
    summary_strategy = str(payload.get("summary_strategy") or "").strip()
    summary_chars = payload.get("summary_chars")
    preserved_recent_messages = payload.get("preserved_recent_messages")
    history_messages_before = payload.get("history_messages_before")
    history_messages_after = payload.get("history_messages_after")
    context_chars_before = payload.get("context_chars_before")
    context_chars_after = payload.get("context_chars_after")
    compacted_history = payload.get("compacted_history")
    compacted_context = payload.get("compacted_context")
    error_detail = str(payload.get("error_detail") or "").strip()
    reason = str(payload.get("reason") or "").strip()

    parts: list[str] = []
    if isinstance(attempt, int):
        parts.append(f"attempt={attempt}")
    if summary_strategy:
        parts.append(f"summary={summary_strategy}")
    if isinstance(summary_chars, int):
        parts.append(f"summary_chars={summary_chars}")
    if isinstance(preserved_recent_messages, int):
        parts.append(f"keep_recent={preserved_recent_messages}")
    if isinstance(history_messages_before, int) and isinstance(history_messages_after, int):
        parts.append(f"history={history_messages_before}->{history_messages_after}")
    if isinstance(context_chars_before, int) and isinstance(context_chars_after, int):
        parts.append(f"context={context_chars_before}->{context_chars_after}")
    if compacted_history is True:
        parts.append("history_compacted=true")
    if compacted_context is True:
        parts.append("context_compacted=true")
    if reason:
        parts.append(f"reason={_fmt_value(reason)}")
    if error_detail:
        parts.append(f"provider={_fmt_value(error_detail)}")
    if not parts:
        return None
    return " ".join(parts)


def _render_turn_plan_details(event: ProgressEvent) -> str | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    planning_mode = str(payload.get("planning_mode") or "").strip()
    if planning_mode.lower() != "plan_only":
        return None
    thinking_level = str(payload.get("thinking_level") or "").strip()
    tool_access_mode = str(payload.get("tool_access_mode") or "").strip()
    available_tools_after_filter = payload.get("available_tools_after_filter")
    selected_skill_names = payload.get("selected_skill_names")

    parts: list[str] = []
    if planning_mode:
        parts.append(f"mode={planning_mode}")
    if thinking_level:
        parts.append(f"thinking={thinking_level}")
    if tool_access_mode:
        parts.append(f"tools={tool_access_mode}")
    if isinstance(selected_skill_names, list):
        normalized_skills = [
            sanitize_terminal_text(str(item).strip())
            for item in selected_skill_names
            if str(item).strip()
        ]
        if normalized_skills:
            parts.append(f"selected_skills={','.join(normalized_skills)}")
    if isinstance(available_tools_after_filter, list):
        parts.append(f"visible_tools={len(available_tools_after_filter)}")
    if not parts:
        return None
    return " ".join(parts)


def _fmt_value(value: object, *, limit: int = 72) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, (int, float)):
        rendered = str(value)
    elif isinstance(value, str):
        rendered = sanitize_terminal_text(value)
    else:
        rendered = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    if len(rendered) > limit:
        rendered = f"{rendered[: limit - 3]}..."
    return rendered


def _status_marker(event: RenderEvent) -> str:
    if event.stage in {"tool_call", "subagent_wait"}:
        return "● "
    return ""


def _is_tool_result_error(event: ProgressEvent | None) -> bool:
    if event is None:
        return False
    result = event.tool_result or {}
    if result.get("ok") is False:
        return True
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return False
    exit_code = payload.get("exit_code")
    return isinstance(exit_code, int) and exit_code != 0
