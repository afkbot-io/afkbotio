"""Tests for turn-level thinking and budget resolution helpers."""

from __future__ import annotations

from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS
from afkbot.services.agent_loop.thinking import resolve_turn_thinking_config


def test_resolve_turn_thinking_config_separates_request_timeout_from_wall_clock_budget() -> None:
    """Very-high turns should keep a larger wall-clock budget than one request timeout."""

    # Arrange
    base_timeout_sec = 1800.0

    # Act
    result = resolve_turn_thinking_config(
        runtime_limit=16,
        base_timeout_sec=base_timeout_sec,
        default_thinking_level="very_high",
        override_thinking_level=None,
        planning_mode="off",
        override_tool_access_mode=None,
        execution_budget_very_high_sec=7200.0,
    )

    # Assert
    assert result.request_timeout_sec == 1800.0
    assert result.wall_clock_budget_sec == 7200.0


def test_resolve_turn_thinking_config_respects_high_execution_budget() -> None:
    """High turns should keep the 30-minute request cap but a separate 1-hour total budget."""

    # Arrange
    base_timeout_sec = 1800.0

    # Act
    result = resolve_turn_thinking_config(
        runtime_limit=10,
        base_timeout_sec=base_timeout_sec,
        default_thinking_level="high",
        override_thinking_level=None,
        planning_mode="off",
        override_tool_access_mode=None,
        execution_budget_high_sec=3600.0,
    )

    # Assert
    assert result.request_timeout_sec == 1800.0
    assert result.wall_clock_budget_sec == 3600.0


def test_resolve_turn_thinking_config_keeps_plan_only_budget_above_request_timeout_cap() -> None:
    """Plan-only turns should retain a larger total budget than one request timeout."""

    # Arrange
    base_timeout_sec = 1800.0

    # Act
    result = resolve_turn_thinking_config(
        runtime_limit=10,
        base_timeout_sec=base_timeout_sec,
        default_thinking_level="high",
        override_thinking_level=None,
        planning_mode="plan_only",
        override_tool_access_mode=None,
        execution_budget_high_sec=3600.0,
    )

    # Assert
    assert result.request_timeout_sec == 1800.0
    assert result.wall_clock_budget_sec == 5400.0


def test_resolve_turn_thinking_config_uses_runtime_limit_when_default_cap_is_raised() -> None:
    """Thinking presets should no longer clamp a full runtime limit down to 10 or 16."""

    # Arrange
    runtime_limit = DEFAULT_LLM_MAX_ITERATIONS

    # Act
    result = resolve_turn_thinking_config(
        runtime_limit=runtime_limit,
        base_timeout_sec=1800.0,
        default_thinking_level="high",
        override_thinking_level=None,
        planning_mode="off",
        override_tool_access_mode=None,
        execution_budget_high_sec=3600.0,
    )

    # Assert
    assert result.max_iterations == DEFAULT_LLM_MAX_ITERATIONS
