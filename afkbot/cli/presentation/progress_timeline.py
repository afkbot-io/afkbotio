"""Reducer for readable CLI progress timeline rendering."""

from __future__ import annotations

from dataclasses import dataclass, replace

from afkbot.cli.presentation.progress_mapper import map_progress_event
from afkbot.cli.presentation.progress_renderer import render_progress_color
from afkbot.cli.presentation.progress_renderer import render_progress_detail, render_progress_event
from afkbot.services.agent_loop.progress_stream import ProgressEvent

_SPINNER_LABELS: dict[str, str] = {
    "thinking": "thinking",
    "planning": "planning",
    "subagent_wait": "waiting subagent",
}
_GROUP_STAGES = {"tool_call", "subagent_wait"}


@dataclass(slots=True)
class ProgressTimelineState:
    """Mutable reducer state for CLI progress timeline."""

    group_seq: int = 0
    open_group_seq: int | None = None
    pending_separator: bool = False
    active_spinner_label: str | None = None


@dataclass(slots=True)
class ProgressRenderFrame:
    """Single render frame produced by timeline reducer."""

    separator_before: bool = False
    color: str = "\033[94m"
    spinner_label: str | None = None
    status_line: str | None = None
    detail_line: str | None = None
    stop_spinner: bool = False


def reduce_progress_event(
    state: ProgressTimelineState,
    event: ProgressEvent,
) -> tuple[ProgressTimelineState, ProgressRenderFrame | None]:
    """Reduce one progress event into side-effect-free UI render intent."""

    mapped = map_progress_event(event)
    if mapped is None:
        return state, None
    color = render_progress_color(mapped)

    if mapped.stage in {"done", "cancelled"}:
        next_state = replace(state, active_spinner_label=None, pending_separator=False)
        return next_state, ProgressRenderFrame(stop_spinner=True, color=color)

    separator_before = state.pending_separator and event.event_type != "tool.result"
    pending_separator = state.pending_separator
    if separator_before:
        pending_separator = False

    if _is_spinner_event(event):
        spinner_label = _decorate_iteration(
            _SPINNER_LABELS.get(mapped.stage, "working"),
            mapped.iteration,
        )
        if spinner_label == state.active_spinner_label and not separator_before:
            return state, None
        next_state = replace(
            state,
            pending_separator=pending_separator,
            active_spinner_label=spinner_label,
        )
        return next_state, ProgressRenderFrame(
            separator_before=separator_before,
            color=color,
            spinner_label=spinner_label,
        )

    status_line = render_progress_event(mapped)
    detail_line = render_progress_detail(event)
    next_group_seq = state.group_seq
    next_open_group_seq = state.open_group_seq

    if mapped.stage in _GROUP_STAGES:
        if event.event_type == "tool.call":
            if mapped.resumed_tool_call and next_open_group_seq is not None:
                status_line = _decorate_group(status_line, next_open_group_seq)
            else:
                next_group_seq += 1
                next_open_group_seq = next_group_seq
                status_line = _decorate_group(status_line, next_group_seq)
        elif event.event_type == "tool.progress" or mapped.live_result:
            group_seq = next_open_group_seq or next_group_seq
            if group_seq > 0:
                status_line = _decorate_group(status_line, group_seq)
        elif event.event_type == "tool.result":
            group_seq = next_open_group_seq or next_group_seq
            if group_seq > 0:
                status_line = _decorate_group(status_line, group_seq)
            next_open_group_seq = None
            pending_separator = True

    status_line = _decorate_iteration(status_line, mapped.iteration)
    next_state = replace(
        state,
        group_seq=next_group_seq,
        open_group_seq=next_open_group_seq,
        pending_separator=pending_separator,
        active_spinner_label=None,
    )
    return next_state, ProgressRenderFrame(
        separator_before=separator_before,
        color=color,
        status_line=status_line,
        detail_line=detail_line,
        stop_spinner=state.active_spinner_label is not None,
    )


def _decorate_group(line: str, group_seq: int) -> str:
    return f"[#{group_seq}] {line}"


def _decorate_iteration(line: str, iteration: int | None) -> str:
    if iteration is None or iteration <= 0:
        return line
    return f"[iter {iteration}] {line}"


def _is_spinner_event(event: ProgressEvent) -> bool:
    # Only synthetic turn.progress stages own the long-lived spinner line.
    # `llm.call.*` events are rendered as regular status/detail frames so
    # heartbeat/timeout information remains visible in the transcript.
    return event.event_type == "turn.progress" and event.stage in _SPINNER_LABELS
