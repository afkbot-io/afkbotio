"""Unit tests for explicit plan-request heuristics."""

from __future__ import annotations

from afkbot.services.agent_loop.planning_policy import is_explicit_plan_request


def test_is_explicit_plan_request_ignores_hyphenated_planning_mentions() -> None:
    """Hyphenated product descriptions must not force a public plan-only turn."""

    # Arrange
    message = (
        "CLI поддерживает planning-режима и инструментальное выполнение, "
        "но сейчас просто создай bootstrap-файлы."
    )

    # Act
    result = is_explicit_plan_request(message)

    # Assert
    assert result is False


def test_is_explicit_plan_request_keeps_plain_planning_requests() -> None:
    """Plain planning requests should still be detected as explicit plan asks."""

    # Arrange
    message = "Use planning mode and return only the plan."

    # Act
    result = is_explicit_plan_request(message)

    # Assert
    assert result is True
