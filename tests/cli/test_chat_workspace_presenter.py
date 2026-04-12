"""Tests for fullscreen chat workspace presenter helpers."""

from __future__ import annotations

from afkbot.cli.presentation.chat_workspace.presenter import (
    build_chat_workspace_progress_entries,
)
from afkbot.cli.presentation.progress_timeline import ProgressTimelineState
from afkbot.services.agent_loop.progress_stream import ProgressEvent


def test_build_chat_workspace_progress_entries_keeps_thinking_spinner_out_of_transcript() -> None:
    """Thinking progress should stay in status strips, not transcript rows."""

    # Arrange
    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=1,
        run_id=1,
        stage="thinking",
        iteration=2,
        tool_name=None,
        event_type="turn.progress",
    )

    # Act
    next_state, entries = build_chat_workspace_progress_entries(
        state,
        event,
        first_progress_entry=True,
    )

    # Assert
    assert next_state.active_spinner_label == "[iter 2] thinking"
    assert entries == ()


def test_build_chat_workspace_progress_entries_skips_pre_iteration_thinking_noise() -> None:
    """Pre-iteration thinking heartbeats should stay out of the transcript."""

    # Arrange
    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=2,
        run_id=1,
        stage="thinking",
        iteration=None,
        tool_name=None,
        event_type="turn.progress",
    )

    # Act
    next_state, entries = build_chat_workspace_progress_entries(
        state,
        event,
        first_progress_entry=True,
    )

    # Assert
    assert next_state is state
    assert entries == ()


def test_build_chat_workspace_progress_entries_skips_llm_internal_ticks() -> None:
    """Low-level LLM call ticks should not clutter the fullscreen transcript."""

    # Arrange
    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=3,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.tick",
        payload={"elapsed_ms": 2500, "timeout_ms": 30000},
    )

    # Act
    next_state, entries = build_chat_workspace_progress_entries(
        state,
        event,
        first_progress_entry=True,
    )

    # Assert
    assert next_state is state
    assert entries == ()


def test_build_chat_workspace_progress_entries_shows_llm_start_details() -> None:
    """Fullscreen chat should surface when one provider request actually starts."""

    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=30,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.start",
        payload={
            "timeout_ms": 30000,
            "queue_wait_ms": 12,
            "reasoning_effort": "medium",
            "available_tool_names": ["file.read", "web.fetch"],
        },
    )

    next_state, entries = build_chat_workspace_progress_entries(
        state,
        event,
        first_progress_entry=True,
    )

    assert next_state.active_spinner_label is None
    assert len(entries) == 2
    assert entries[0].text == "thinking..."
    assert entries[0].accent == "thinking"
    assert entries[1].text == "llm=start timeout_ms=30000 reasoning=medium visible_tools=2"
    assert entries[1].accent == "detail"


def test_build_chat_workspace_progress_entries_shows_llm_queue_details() -> None:
    """Fullscreen chat should show when a request is waiting for the shared LLM lane."""

    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=29,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.queued",
        payload={
            "timeout_ms": 120000,
            "reasoning_effort": "medium",
            "available_tool_names": ["file.read"],
        },
    )

    next_state, entries = build_chat_workspace_progress_entries(
        state,
        event,
        first_progress_entry=True,
    )

    assert next_state.active_spinner_label is None
    assert len(entries) == 2
    assert entries[0].text == "thinking..."
    assert entries[0].accent == "thinking"
    assert entries[1].text == "llm=queued timeout_ms=120000 reasoning=medium visible_tools=1"
    assert entries[1].accent == "detail"


def test_build_chat_workspace_progress_entries_shows_llm_timeout_details() -> None:
    """Fullscreen chat should surface provider timeout failures instead of looking idle."""

    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=31,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.timeout",
        payload={
            "elapsed_ms": 30000,
            "timeout_ms": 30000,
            "error_code": "llm_timeout",
        },
    )

    next_state, entries = build_chat_workspace_progress_entries(
        state,
        event,
        first_progress_entry=True,
    )

    assert next_state.active_spinner_label is None
    assert len(entries) == 2
    assert entries[0].text == "thinking..."
    assert entries[0].accent == "thinking"
    assert entries[1].text == "llm=timeout elapsed_ms=30000 timeout_ms=30000 error=llm_timeout"
    assert entries[1].accent == "detail"


def test_build_chat_workspace_progress_entries_shows_llm_done_errors() -> None:
    """Provider-side failures returned as responses should not stay hidden."""

    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=32,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.done",
        payload={
            "elapsed_ms": 1200,
            "timeout_ms": 120000,
            "response_kind": "final",
            "error_code": "llm_provider_network_error",
        },
    )

    next_state, entries = build_chat_workspace_progress_entries(
        state,
        event,
        first_progress_entry=True,
    )

    assert next_state.active_spinner_label is None
    assert len(entries) == 2
    assert entries[0].text == "thinking..."
    assert entries[0].accent == "thinking"
    assert (
        entries[1].text == "llm=done elapsed_ms=1200 timeout_ms=120000 kind=final "
        "error=llm_provider_network_error"
    )
    assert entries[1].accent == "detail"


def test_build_chat_workspace_progress_entries_shows_context_compaction_steps() -> None:
    """Visible compaction events should reach the fullscreen transcript."""

    state = ProgressTimelineState()
    event = ProgressEvent(
        event_id=4,
        run_id=1,
        stage="thinking",
        iteration=1,
        tool_name=None,
        event_type="llm.call.compaction_done",
        payload={"attempt": 1, "summary_strategy": "hybrid_llm_v1"},
    )

    next_state, entries = build_chat_workspace_progress_entries(
        state,
        event,
        first_progress_entry=True,
    )

    assert next_state.active_spinner_label is None
    assert len(entries) == 2
    assert entries[0].text == "----- Context automatically compacted -----"
    assert entries[0].accent == "thinking"
    assert entries[1].text == "attempt=1 summary=hybrid_llm_v1"
    assert entries[1].accent == "detail"


def test_build_chat_workspace_progress_entries_renders_tool_status_and_detail() -> None:
    """Tool progress should keep the old AFK status/detail transcript shape."""

    # Arrange
    state = ProgressTimelineState()
    call_event = ProgressEvent(
        event_id=10,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    progress_event = ProgressEvent(
        event_id=11,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    progress_event.attach_tool_details(
        tool_progress={"preview_lines": ["stdout | one", "stdout | two"]}
    )

    # Act
    next_state, call_entries = build_chat_workspace_progress_entries(
        state,
        call_event,
        first_progress_entry=True,
    )
    _, progress_entries = build_chat_workspace_progress_entries(
        next_state,
        progress_event,
        first_progress_entry=False,
    )

    # Assert
    assert len(call_entries) == 1
    assert call_entries[0].kind == "assistant"
    assert call_entries[0].text == "[#1] ● calling tool: bash.exec"
    assert call_entries[0].accent == "tool"
    assert call_entries[0].spacing_before == "normal"
    assert len(progress_entries) == 3
    assert progress_entries[0].kind == "assistant"
    assert progress_entries[0].text == "[#1] ● tool running: bash.exec"
    assert progress_entries[0].accent == "tool"
    assert progress_entries[0].spacing_before == "tight"
    assert progress_entries[1].kind == "assistant"
    assert progress_entries[1].text == "  stdout | one"
    assert progress_entries[1].accent == "detail"
    assert progress_entries[1].spacing_before == "tight"
    assert progress_entries[2].kind == "assistant"
    assert progress_entries[2].text == "  stdout | two"
    assert progress_entries[2].accent == "detail"
    assert progress_entries[2].spacing_before == "tight"


def test_build_chat_workspace_progress_entries_only_appends_new_preview_tail_lines() -> None:
    """Rolling preview updates should not replay old tool lines in transcript."""

    # Arrange
    state = ProgressTimelineState()
    call_event = ProgressEvent(
        event_id=20,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="bash.exec",
        event_type="tool.call",
    )
    first_progress = ProgressEvent(
        event_id=21,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    first_progress.attach_tool_details(
        tool_progress={"preview_lines": ["stdout | one", "stdout | two"]}
    )
    second_progress = ProgressEvent(
        event_id=22,
        run_id=1,
        stage="tool_call",
        iteration=1,
        tool_name="bash.exec",
        event_type="tool.progress",
    )
    second_progress.attach_tool_details(
        tool_progress={"preview_lines": ["stdout | one", "stdout | two", "stdout | three"]}
    )

    # Act
    state, _ = build_chat_workspace_progress_entries(state, call_event, first_progress_entry=True)
    state, first_entries = build_chat_workspace_progress_entries(
        state,
        first_progress,
        first_progress_entry=False,
    )
    _, second_entries = build_chat_workspace_progress_entries(
        state,
        second_progress,
        first_progress_entry=False,
    )

    # Assert
    assert [entry.text for entry in first_entries] == [
        "[#1] ● tool running: bash.exec",
        "  stdout | one",
        "  stdout | two",
    ]
    assert [entry.text for entry in second_entries] == [
        "[#1] ● tool running: bash.exec",
        "  stdout | three",
    ]
