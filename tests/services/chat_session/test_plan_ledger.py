"""Tests for structured chat plan capture helpers."""

from __future__ import annotations

from afkbot.services.chat_session.plan_ledger import capture_chat_plan


def test_capture_chat_plan_parses_checkbox_ordered_and_bullet_steps() -> None:
    """Plan capture should normalize common checklist and list formats."""

    # Arrange
    text = """
    [x] Review scope
    1. Implement queue
    - Verify tests
    """

    # Act
    snapshot = capture_chat_plan(text)

    # Assert
    assert snapshot is not None
    assert snapshot.step_count == 3
    assert snapshot.steps[0].text == "Review scope"
    assert snapshot.steps[0].completed is True
    assert snapshot.steps[1].text == "Implement queue"
    assert snapshot.steps[1].completed is False
    assert snapshot.steps[2].text == "Verify tests"


def test_capture_chat_plan_returns_none_for_blank_text() -> None:
    """Plan capture should reject empty assistant output."""

    # Arrange
    text = "   \n\n"

    # Act
    snapshot = capture_chat_plan(text)

    # Assert
    assert snapshot is None
