"""Tests for fullscreen chat workspace presenter helpers."""

from __future__ import annotations

from afkbot.cli.presentation.chat_workspace.presenter import (
    build_chat_workspace_progress_entries,
)
from afkbot.cli.presentation.progress_timeline import ProgressTimelineState
from afkbot.services.agent_loop.progress_stream import ProgressEvent


def test_build_chat_workspace_progress_entries_renders_thinking_spinner_line() -> None:
    """Thinking progress should become one transcript activity entry."""

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
    assert len(entries) == 1
    assert entries[0].kind == "assistant"
    assert entries[0].text == "[iter 2] thinking..."
    assert entries[0].accent == "thinking"
    assert entries[0].spacing_before == "normal"


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
    assert call_entries[0].text == "[iter 1] [#1] calling tool: bash.exec"
    assert call_entries[0].accent == "tool"
    assert call_entries[0].spacing_before == "normal"
    assert len(progress_entries) == 2
    assert progress_entries[0].kind == "assistant"
    assert progress_entries[0].text == "[iter 1] [#1] tool running: bash.exec"
    assert progress_entries[0].accent == "tool"
    assert progress_entries[0].spacing_before == "tight"
    assert progress_entries[1].kind == "assistant"
    assert progress_entries[1].text == "stdout | two"
    assert progress_entries[1].accent == "detail"
    assert progress_entries[1].spacing_before == "tight"
