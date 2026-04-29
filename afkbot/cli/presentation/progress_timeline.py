"""Reducer for readable CLI progress timeline rendering."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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
    pending_separator: bool = False
    active_spinner_label: str | None = None
    tool_group_seq_by_key: dict[str, int] = field(default_factory=dict)
    last_tool_preview_group_seq: int | None = None
    last_tool_preview_lines: tuple[str, ...] = ()


@dataclass(slots=True)
class ProgressRenderFrame:
    """Single render frame produced by timeline reducer."""

    separator_before: bool = False
    color: str = "\033[94m"
    spinner_label: str | None = None
    status_line: str | None = None
    detail_line: str | None = None
    group_seq: int | None = None
    stop_spinner: bool = False


def reduce_progress_event(
    state: ProgressTimelineState,
    event: ProgressEvent,
) -> tuple[ProgressTimelineState, ProgressRenderFrame | None]:
    """Reduce one progress event into side-effect-free UI render intent."""

    mapped = map_progress_event(event)
    if mapped is None:
        return state, None
    color = render_progress_color(mapped, progress_event=event)

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
    next_tool_group_seq_by_key = dict(state.tool_group_seq_by_key)
    decorated_group_seq: int | None = None

    if mapped.stage in _GROUP_STAGES:
        group_keys = _tool_group_keys(event)
        existing_group_seq = next(
            (
                next_tool_group_seq_by_key[key]
                for key in group_keys
                if key in next_tool_group_seq_by_key
            ),
            None,
        )
        if event.event_type == "tool.call":
            if existing_group_seq is None:
                next_group_seq += 1
                existing_group_seq = next_group_seq
            decorated_group_seq = existing_group_seq
            for key in group_keys:
                next_tool_group_seq_by_key[key] = existing_group_seq
        elif event.event_type in {"tool.progress", "tool.result"} or mapped.live_result:
            decorated_group_seq = existing_group_seq
            if decorated_group_seq is None and next_group_seq > 0:
                decorated_group_seq = next_group_seq
            if decorated_group_seq is not None:
                for key in group_keys:
                    next_tool_group_seq_by_key[key] = decorated_group_seq

        if decorated_group_seq is not None:
            status_line = _decorate_group(status_line, decorated_group_seq)

        if event.event_type == "tool.result" and not mapped.live_result:
            if decorated_group_seq is not None:
                next_tool_group_seq_by_key = {
                    key: group_seq
                    for key, group_seq in next_tool_group_seq_by_key.items()
                    if group_seq != decorated_group_seq
                }
            pending_separator = True

    status_line = _decorate_iteration(status_line, mapped.iteration)
    next_state = replace(
        state,
        group_seq=next_group_seq,
        pending_separator=pending_separator,
        active_spinner_label=None,
        tool_group_seq_by_key=next_tool_group_seq_by_key,
    )
    if next_state == state:
        return state, ProgressRenderFrame(
            separator_before=separator_before,
            color=color,
            status_line=status_line,
            detail_line=detail_line,
            group_seq=decorated_group_seq,
            stop_spinner=state.active_spinner_label is not None,
        )
    return next_state, ProgressRenderFrame(
        separator_before=separator_before,
        color=color,
        status_line=status_line,
        detail_line=detail_line,
        group_seq=decorated_group_seq,
        stop_spinner=state.active_spinner_label is not None,
    )


def _decorate_group(line: str, group_seq: int) -> str:
    return f"[#{group_seq}] {line}"


def _decorate_iteration(line: str, iteration: int | None) -> str:
    if line.startswith("----- "):
        return line
    if iteration is None or iteration <= 0:
        return line
    return f"[iter {iteration}] {line}"


def _is_spinner_event(event: ProgressEvent) -> bool:
    # Only synthetic turn.progress stages own the long-lived spinner line.
    # `llm.call.*` events are rendered as regular status/detail frames so
    # heartbeat/timeout information remains visible in the transcript.
    return (
        event.event_type == "turn.progress"
        and event.stage in _SPINNER_LABELS
        and event.iteration is not None
    )


def _tool_group_keys(event: ProgressEvent) -> tuple[str, ...]:
    keys: list[str] = []
    call_id = (event.call_id or "").strip()
    if call_id:
        keys.append(f"call:{call_id}")

    session_id = ""
    if event.event_type == "tool.call":
        params = event.tool_call_params or {}
        session_id = str(params.get("session_id") or "").strip()
    elif event.event_type == "tool.result":
        result = event.tool_result or {}
        payload = result.get("payload")
        if isinstance(payload, dict):
            session_id = str(payload.get("session_id") or "").strip()
    if session_id:
        keys.append(f"session:{session_id}")
    return tuple(dict.fromkeys(keys))
