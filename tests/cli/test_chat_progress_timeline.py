"""Tests for CLI progress timeline reducer."""

from __future__ import annotations

from afkbot.cli.presentation import ProgressTimelineState, reduce_progress_event
from afkbot.services.agent_loop.progress_stream import ProgressEvent


def test_timeline_groups_tool_call_and_result_then_separates_next_stage() -> None:
    """Reducer should keep tool call/result in one group and separate next phase."""

    state = ProgressTimelineState()

    call_event = ProgressEvent(
        event_id=1,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="debug.echo",
        event_type="tool.call",
    )
    state, call_frame = reduce_progress_event(state, call_event)
    assert call_frame is not None
    assert call_frame.status_line == "[iter 1] [#1] calling tool: debug.echo"
    assert call_frame.separator_before is False

    result_event = ProgressEvent(
        event_id=2,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="debug.echo",
        event_type="tool.result",
    )
    result_event.attach_tool_details(tool_result={"ok": True, "payload": {"echo": "ok"}})
    state, result_frame = reduce_progress_event(state, result_event)
    assert result_frame is not None
    assert result_frame.status_line == "[iter 1] [#1] tool completed: debug.echo"
    assert result_frame.separator_before is False

    next_event = ProgressEvent(
        event_id=3,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="turn.progress",
    )
    state, next_frame = reduce_progress_event(state, next_event)
    assert next_frame is not None
    assert next_frame.separator_before is True
    assert next_frame.spinner_label == "[iter 1] thinking"


def test_timeline_keeps_tool_progress_inside_existing_tool_group() -> None:
    """Reducer should keep tool.progress frames inside the current tool group."""

    # Arrange
    state = ProgressTimelineState()
    call_event = ProgressEvent(
        event_id=10,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    progress_event = ProgressEvent(
        event_id=11,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    progress_event.attach_tool_details(
        tool_progress={"preview_lines": ["stdout | two", "stdout | three"]}
    )

    # Act
    state, _ = reduce_progress_event(state, call_event)
    _, progress_frame = reduce_progress_event(state, progress_event)

    # Assert
    assert progress_frame is not None
    assert progress_frame.status_line == "[iter 2] [#1] tool running: bash.exec"
    assert progress_frame.separator_before is False
    assert progress_frame.detail_line == "stdout | three"


def test_timeline_keeps_live_tool_result_and_resume_call_in_same_group() -> None:
    """Interactive live-session updates should not close the tool group before the final result."""

    # Arrange
    state = ProgressTimelineState()
    call_event = ProgressEvent(
        event_id=20,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    live_result = ProgressEvent(
        event_id=21,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.result",
    )
    live_result.attach_tool_details(
        tool_result={"ok": True, "payload": {"running": True, "session_id": "bash-live-1"}}
    )
    resume_call = ProgressEvent(
        event_id=22,
        run_id=1,
        stage="tool_call",
        iteration=3,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    resume_call.attach_tool_details(tool_call_params={"session_id": "bash-live-1", "chars": "y\n"})
    final_result = ProgressEvent(
        event_id=23,
        run_id=1,
        stage="tool_call",
        iteration=3,
        tool_name="bash.exec",
        event_type="tool.result",
    )
    final_result.attach_tool_details(tool_result={"ok": True, "payload": {"exit_code": 0}})

    # Act
    state, call_frame = reduce_progress_event(state, call_event)
    state, live_frame = reduce_progress_event(state, live_result)
    state, resume_frame = reduce_progress_event(state, resume_call)
    _, final_frame = reduce_progress_event(state, final_result)

    # Assert
    assert call_frame is not None
    assert live_frame is not None
    assert resume_frame is not None
    assert final_frame is not None
    assert live_frame.status_line == "[iter 2] [#1] tool running: bash.exec"
    assert resume_frame.status_line == "[iter 3] [#1] calling tool: bash.exec"
    assert final_frame.status_line == "[iter 3] [#1] tool completed: bash.exec"


def test_timeline_dedupes_identical_spinner_updates() -> None:
    """Reducer should ignore repeated spinner events with unchanged label."""

    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=1,
        run_id=1,
        stage="planning",
        iteration=2,
        tool_name=None,
        event_type="turn.progress",
    )

    state, first = reduce_progress_event(state, event)
    assert first is not None
    assert first.spinner_label == "[iter 2] planning"

    state, second = reduce_progress_event(state, event)
    assert second is None


def test_timeline_done_stops_spinner() -> None:
    """Done/cancel events should request spinner stop without status line."""

    state = ProgressTimelineState()
    progress = ProgressEvent(
        event_id=1,
        run_id=1,
        stage="thinking",
        iteration=2,
        tool_name=None,
        event_type="turn.progress",
    )
    state, _ = reduce_progress_event(state, progress)

    done = ProgressEvent(
        event_id=2,
        run_id=1,
        stage="done",
        iteration=None,
        tool_name=None,
        event_type="turn.finalize",
    )
    _, frame = reduce_progress_event(state, done)
    assert frame is not None
    assert frame.stop_spinner is True
    assert frame.status_line is None


def test_timeline_no_iteration_prefix_for_iteration_zero() -> None:
    """Iteration zero should not add extra status prefixes."""

    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=1,
        run_id=1,
        stage="thinking",
        iteration=0,
        tool_name=None,
        event_type="turn.progress",
    )
    _, frame = reduce_progress_event(state, event)
    assert frame is not None
    assert frame.spinner_label == "thinking"


def test_timeline_renders_llm_tick_as_status_line_not_spinner() -> None:
    """LLM heartbeat events should be visible status frames, not deduped spinner updates."""

    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=2,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.tick",
        payload={"elapsed_ms": 3000, "timeout_ms": 30000},
    )
    _, frame = reduce_progress_event(state, event)
    assert frame is not None
    assert frame.spinner_label is None
    assert frame.status_line == "[iter 1] thinking..."
    assert frame.detail_line == "llm=tick elapsed_ms=3000 timeout_ms=30000"
