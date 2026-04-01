"""Presentation helpers for fullscreen chat workspace state and transcript."""

from __future__ import annotations

from afkbot.cli.presentation.progress_renderer import render_progress_detail_lines
from afkbot.cli.presentation.progress_timeline import ProgressTimelineState, reduce_progress_event
from afkbot.cli.presentation.chat_plan_renderer import render_chat_plan
from afkbot.cli.presentation.chat_workspace.layout import ChatWorkspaceSurfaceState
from afkbot.cli.presentation.chat_workspace.toolbar import (
    build_chat_workspace_footer,
    build_chat_workspace_status_line,
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
    return ChatWorkspaceSurfaceState(
        status_lines=((status_line,) if status_line else ()),
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

    if event.stage == "thinking":
        next_state, _ = reduce_progress_event(state, event)
        return next_state, ()

    if event.event_type.startswith("llm.call."):
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
                text=f"• {cleaned_status_line}",
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
        entries.append(
            ChatWorkspaceTranscriptEntry(
                kind="assistant",
                text=cleaned_status_line,
                accent=_accent_for_stage(event.stage, final_event=event.event_type == "tool.result"),
                spacing_before=first_spacing if not entries else "tight",
            )
        )
    if is_tool_event:
        for detail_line in render_progress_detail_lines(event):
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
) -> ChatWorkspaceAccent:
    if stage == "thinking":
        return "thinking"
    if stage == "planning":
        return "planning"
    if stage in {"tool_call", "subagent_wait"}:
        return "tool"
    if stage == "cancelled":
        return "error"
    if stage == "done" or final_event:
        return "success"
    return "detail"
