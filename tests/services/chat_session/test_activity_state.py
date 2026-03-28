"""Tests for chat-session activity snapshots."""

from __future__ import annotations

from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.chat_session.activity_state import (
    capture_chat_activity,
    starting_chat_activity,
)


def test_starting_chat_activity_is_running() -> None:
    """A new turn should reset activity to a deterministic running snapshot."""

    # Arrange

    # Act
    activity = starting_chat_activity()

    # Assert
    assert activity.summary == "starting"
    assert activity.running is True


def test_capture_chat_activity_marks_live_tool_result_as_running() -> None:
    """Live tool-result payloads should remain in running state for the REPL toolbar."""

    # Arrange
    event = ProgressEvent(
        event_id=1,
        run_id=1,
        stage="tool_call",
        iteration=2,
        tool_name="bash.exec",
        event_type="tool.result",
    )
    event.attach_tool_details(
        tool_result={
            "ok": True,
            "payload": {
                "running": True,
                "session_id": "bash-1",
                "stdout": "waiting for input",
            },
        }
    )

    # Act
    activity = capture_chat_activity(event)

    # Assert
    assert activity is not None
    assert activity.summary == "tool: bash.exec"
    assert activity.detail == "waiting for input"
    assert activity.running is True


def test_capture_chat_activity_marks_completed_subagent_as_not_running() -> None:
    """Completed subagent waits should stop showing as actively running."""

    # Arrange
    event = ProgressEvent(
        event_id=2,
        run_id=1,
        stage="subagent_wait",
        iteration=1,
        tool_name="reviewer",
        event_type="tool.result",
    )

    # Act
    activity = capture_chat_activity(event)

    # Assert
    assert activity is not None
    assert activity.summary == "subagent done: reviewer"
    assert activity.running is False
