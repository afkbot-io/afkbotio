"""Presentation helpers for fullscreen chat workspace state and transcript."""

from __future__ import annotations

from dataclasses import replace

from afkbot.cli.presentation.progress_renderer import render_progress_detail_lines
from afkbot.cli.presentation.progress_timeline import ProgressTimelineState, reduce_progress_event
from afkbot.cli.presentation.chat_plan_renderer import render_chat_plan
from afkbot.cli.presentation.chat_workspace.layout import ChatWorkspaceSurfaceState
from afkbot.cli.presentation.chat_workspace.toolbar import (
    build_chat_workspace_footer,
    build_chat_workspace_status_line,
    build_chat_workspace_session_line,
    build_chat_workspace_queue_lines,
)
from afkbot.cli.presentation.chat_workspace.transcript import (
    ChatWorkspaceAccent,
    ChatWorkspaceSpacing,
    ChatWorkspaceTranscriptEntry,
)
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.chat_session.session_state import ChatReplSessionState
from afkbot.services.chat_session.turn_flow import ChatTurnOutcome

_VISIBLE_LLM_CALL_EVENT_TYPES = frozenset(
    {
        "llm.call.queued",
        "llm.call.start",
        "llm.call.timeout",
        "llm.call.error",
        "llm.call.compaction_start",
        "llm.call.compaction_done",
        "llm.call.compaction_failed",
    }
)


def build_chat_workspace_surface_state(
    state: ChatReplSessionState,
    *,
    status_marker: str | None = None,
) -> ChatWorkspaceSurfaceState:
    """Project session state into Codex-like stacked workspace surfaces."""

    status_line = build_chat_workspace_status_line(
        state,
        status_marker=status_marker,
    )
    session_line = build_chat_workspace_session_line(state)
    return ChatWorkspaceSurfaceState(
        status_lines=tuple(
            line
            for line in (session_line, status_line)
            if line
        ),
        queue_lines=build_chat_workspace_queue_lines(state),
    )


def build_chat_workspace_toolbar_text(state: ChatReplSessionState) -> str:
    """Render the fullscreen footer text for the current session state."""

    return build_chat_workspace_footer(state)


def build_chat_workspace_user_entry(message: str) -> ChatWorkspaceTranscriptEntry:
    """Build one transcript entry for a submitted user message."""

    return ChatWorkspaceTranscriptEntry(kind="user", text=message)


def build_chat_workspace_notice_entry(message: str) -> ChatWorkspaceTranscriptEntry:
    """Build one transcript entry for a local notice or control response."""

    return ChatWorkspaceTranscriptEntry(kind="notice", text=message)


def build_chat_workspace_progress_entries(
    state: ProgressTimelineState,
    event: ProgressEvent,
    *,
    first_progress_entry: bool,
) -> tuple[ProgressTimelineState, tuple[ChatWorkspaceTranscriptEntry, ...]]:
    """Convert one progress event into zero or more transcript entries."""

    visible_llm_call_event = _is_visible_llm_call_event(event)
    if event.stage == "thinking" and not visible_llm_call_event:
        next_state, _ = reduce_progress_event(state, event)
        return next_state, ()

    if event.event_type.startswith("llm.call.") and not visible_llm_call_event:
        return state, ()

    next_state, frame = reduce_progress_event(state, event)
    if frame is None:
        return next_state, ()

    entries: list[ChatWorkspaceTranscriptEntry] = []
    is_tool_event = event.stage in {"tool_call", "subagent_wait"}
    is_tool_call = event.stage == "tool_call" and event.event_type == "tool.call"

    first_spacing: ChatWorkspaceSpacing = (
        "normal" if first_progress_entry or frame.separator_before else "tight"
    )
    cleaned_status_line = (
        _strip_progress_iteration_prefix(frame.status_line)
        if frame.status_line is not None
        else None
    )

    if is_tool_call and cleaned_status_line is not None:
        entries.append(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=cleaned_status_line,
                accent=_accent_for_stage(event.stage),
                spacing_before=first_spacing,
            )
        )
    if frame.spinner_label is not None and event.stage not in {"thinking", "planning"}:
        entries.append(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=f"{frame.spinner_label}...",
                accent=_accent_for_stage(event.stage),
                spacing_before=first_spacing,
            )
        )
    if cleaned_status_line is not None and not is_tool_call:
        final_tool_result = event.event_type == "tool.result"
        entries.append(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=cleaned_status_line,
                accent=_accent_for_stage(
                    event.stage,
                    final_event=final_tool_result,
                    final_error=final_tool_result and _tool_result_is_error(event),
                ),
                spacing_before=first_spacing if not entries else "tight",
            )
        )
    if is_tool_event:
        detail_lines = render_progress_detail_lines(event)
        preview_lines = detail_lines
        if event.event_type == "tool.progress":
            group_seq = frame.group_seq
            previous_lines = (
                state.last_tool_preview_lines
                if group_seq is not None and state.last_tool_preview_group_seq == group_seq
                else ()
            )
            detail_lines = _new_tool_preview_lines(previous_lines, preview_lines)
            next_state = replace(
                next_state,
                last_tool_preview_group_seq=group_seq,
                last_tool_preview_lines=preview_lines,
            )
        elif event.event_type == "tool.result" and not _tool_result_is_live(event):
            next_state = replace(
                next_state,
                last_tool_preview_group_seq=None,
                last_tool_preview_lines=(),
            )
        for detail_line in detail_lines:
            entries.append(
                ChatWorkspaceTranscriptEntry(
                    kind="assistant",
                    text=f"  {detail_line}",
                    accent="detail",
                    spacing_before="tight",
                )
            )
    elif frame.detail_line is not None:
        entries.append(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=frame.detail_line,
                accent="detail",
                spacing_before="tight",
            )
        )
    return next_state, tuple(entries)


def _is_visible_llm_call_event(event: ProgressEvent) -> bool:
    """Return whether a low-level LLM event should reach the chat transcript."""

    if event.event_type in _VISIBLE_LLM_CALL_EVENT_TYPES:
        return True
    if event.event_type != "llm.call.done":
        return False
    payload = event.payload if isinstance(event.payload, dict) else {}
    return bool(str(payload.get("error_code") or "").strip())


def _strip_progress_iteration_prefix(value: str) -> str:
    """Remove `[iter N] ` prefix so transcript stays compact."""

    if not value.startswith("[iter "):
        return value
    close = value.find("] ")
    if close == -1:
        return value
    prefix = value[1:close]
    if not prefix.startswith("iter "):
        return value
    iteration = prefix[len("iter ") :]
    if not iteration.isdigit():
        return value
    return value[close + 2 :]


def _new_tool_preview_lines(
    previous_lines: tuple[str, ...],
    current_lines: tuple[str, ...],
) -> tuple[str, ...]:
    """Return only newly observed tail lines for one rolling preview window."""

    if not current_lines:
        return ()
    if not previous_lines:
        return current_lines
    max_overlap = min(len(previous_lines), len(current_lines))
    for overlap in range(max_overlap, 0, -1):
        if previous_lines[-overlap:] == current_lines[:overlap]:
            return current_lines[overlap:]
    return current_lines


def build_chat_workspace_outcome_entry(
    outcome: ChatTurnOutcome | None,
) -> ChatWorkspaceTranscriptEntry | None:
    """Convert one completed chat turn into a fullscreen transcript entry."""

    if outcome is None:
        return None
    if outcome.final_output == "none":
        return None
    if outcome.final_output == "plan" and outcome.plan_snapshot is not None:
        return ChatWorkspaceTranscriptEntry(
            kind="plan",
            text=render_chat_plan(
                outcome.plan_snapshot,
                phase="planned",
                include_header=False,
                leading_blank_line=False,
                ansi=False,
            ),
        )
    return ChatWorkspaceTranscriptEntry(
        kind="assistant",
        text=(outcome.result.envelope.message or "").strip() or "(empty response)",
    )


def _accent_for_stage(
    stage: str,
    *,
    final_event: bool = False,
    final_error: bool = False,
) -> ChatWorkspaceAccent:
    if stage == "thinking":
        return "thinking"
    if stage == "planning":
        return "planning"
    if stage in {"tool_call", "subagent_wait"}:
        if final_event and final_error:
            return "error"
        if final_event:
            return "success"
        return "tool"
    if stage == "cancelled":
        return "error"
    if stage == "done":
        return "success"
    return "detail"


def _tool_result_is_error(event: ProgressEvent) -> bool:
    result = event.tool_result or {}
    if result.get("ok") is False:
        return True
    payload = result.get("payload")
    if not isinstance(payload, dict):
        return False
    exit_code = payload.get("exit_code")
    return isinstance(exit_code, int) and exit_code != 0


def _tool_result_is_live(event: ProgressEvent) -> bool:
    result = event.tool_result or {}
    payload = result.get("payload")
    return isinstance(payload, dict) and payload.get("running") is True
