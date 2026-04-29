"""Status and footer composition helpers for the interactive chat workspace."""

from __future__ import annotations

import os
from time import monotonic

from afkbot.cli.presentation.chat_plan_status import (
    plan_summary_for_chat_workspace,
    stored_plan_status_for_chat_workspace,
)
from afkbot.cli.presentation.terminal_text import sanitize_terminal_text
from afkbot.services.chat_session.activity_state import ChatActivitySnapshot
from afkbot.services.chat_session.session_state import ChatReplSessionState

DEFAULT_CHAT_WORKSPACE_FOOTER = "/ commands · $ capabilities · @ files"


def build_chat_workspace_session_line(state: ChatReplSessionState) -> str | None:
    """Render the current chat session identity for the prompt status surface."""

    session_id = sanitize_terminal_text((state.session_id or "").strip())
    if not session_id:
        return None
    session_label = sanitize_terminal_text((state.session_label or "").strip())
    if session_label and session_label != session_id:
        return f"◦ Session {session_label} · id={session_id}"
    return f"◦ Session {session_id}"


def build_chat_workspace_status_line(
    state: ChatReplSessionState,
    *,
    status_marker: str | None = None,
) -> str:
    """Render the compact working/idle strip above the composer."""

    if state.active_turn:
        elapsed = _elapsed_label(state)
        line = "• Working"
        if elapsed is not None:
            line += f" ({elapsed} • esc to interrupt)"
        else:
            line += " (esc to interrupt)"
        snapshot = state.latest_activity
        activity = activity_line_for_chat_workspace(snapshot)
        if status_marker is not None and activity is not None:
            line += f" · {status_marker} {activity}"
        elif activity is not None:
            line += f" · {activity}"
        if state.queued_messages > 0:
            noun = "message" if state.queued_messages == 1 else "messages"
            line += f" · queued {state.queued_messages} {noun}"
        return line
    return ""


def build_chat_workspace_queue_lines(state: ChatReplSessionState) -> tuple[str, ...]:
    """Render the queued-message preview lines above the composer."""

    lines: list[str] = []
    plan_line = _active_plan_line(state)
    if plan_line is not None:
        lines.append(plan_line)
    if state.queued_messages <= 0:
        return tuple(lines)
    label = "message" if state.queued_messages == 1 else "messages"
    lines.append(f"◦ Queued {state.queued_messages} {label} for the next turn.")
    return tuple(lines)


def build_chat_workspace_footer(state: ChatReplSessionState) -> str:
    """Build the compact footer text for the current workspace mode."""

    mode_tokens: list[str] = []
    try:
        cwd = sanitize_terminal_text(os.getcwd().strip())
    except OSError:
        cwd = ""
    if cwd:
        mode_tokens.append(f"cwd={cwd}")
    if state.planning_mode != state.default_planning_mode:
        mode_tokens.append(f"plan={state.planning_mode}")
    if state.thinking_level != state.default_thinking_level:
        mode_tokens.append(f"thinking={state.thinking_level or 'default'}")
    if not mode_tokens:
        return DEFAULT_CHAT_WORKSPACE_FOOTER
    return f"{DEFAULT_CHAT_WORKSPACE_FOOTER} · {' · '.join(mode_tokens)}"


def compact_activity_status_for_chat_workspace(state: ChatReplSessionState) -> str:
    """Render one truncated activity summary for compact status surfaces."""

    activity = activity_line_for_chat_workspace(state.latest_activity)
    if activity is None:
        return "idle"
    return truncate_activity_summary(activity)


def activity_line_for_chat_workspace(snapshot: ChatActivitySnapshot | None) -> str | None:
    """Render one human-facing activity label for the working strip."""

    if snapshot is None:
        return None
    if snapshot.summary == "starting":
        return None
    if snapshot.stage == "thinking":
        return "thinking..."
    if snapshot.stage == "planning":
        return "planning..."
    if snapshot.stage == "tool_call":
        return _toolish_activity_label(
            running_prefix="calling tool",
            completed_prefix="tool completed",
            fallback="tool",
            snapshot=snapshot,
        )
    if snapshot.stage == "subagent_wait":
        return _toolish_activity_label(
            running_prefix="waiting subagent",
            completed_prefix="subagent completed",
            fallback="subagent",
            snapshot=snapshot,
        )
    if snapshot.stage == "done":
        return "response ready"
    if snapshot.stage == "cancelled":
        return "cancelled"
    return truncate_activity_summary(sanitize_terminal_text(snapshot.summary))


def truncate_activity_summary(value: str) -> str:
    """Truncate one activity label for narrow prompt surfaces."""

    if len(value) <= 28:
        return value
    return value[:25].rstrip() + "..."


def _elapsed_label(state: ChatReplSessionState) -> str | None:
    started_at = state.active_turn_started_at
    if started_at is None:
        return None
    elapsed_seconds = max(0, int(monotonic() - started_at))
    if elapsed_seconds < 60:
        return f"{elapsed_seconds}s"
    if elapsed_seconds < 3_600:
        minutes, seconds = divmod(elapsed_seconds, 60)
        return f"{minutes}m {seconds:02d}s"
    hours, remainder = divmod(elapsed_seconds, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s"


def _toolish_activity_label(
    *,
    running_prefix: str,
    completed_prefix: str,
    fallback: str,
    snapshot: ChatActivitySnapshot,
) -> str:
    name = sanitize_terminal_text(snapshot.summary.strip())
    for prefix in ("tool: ", "tool done: ", "subagent: ", "subagent done: "):
        if name.startswith(prefix):
            name = name.removeprefix(prefix).strip()
            break
    if not name:
        name = fallback
    prefix = running_prefix if snapshot.running else completed_prefix
    return f"{prefix}: {name}"


def _active_plan_line(state: ChatReplSessionState) -> str | None:
    if (
        not state.active_turn
        or state.latest_plan is None
        or state.latest_plan_phase not in {"planned", "executing"}
    ):
        return None
    status = stored_plan_status_for_chat_workspace(
        state.latest_plan,
        phase=state.latest_plan_phase,
    )
    summary = plan_summary_for_chat_workspace(state.latest_plan)
    parts = [f"◦ Plan {status}"]
    if summary != "none":
        parts.append(summary)
    return sanitize_terminal_text(" · ".join(parts))
