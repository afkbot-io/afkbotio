"""Turn-scoped planning and thinking-level helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS
from afkbot.services.llm_timeout_policy import (
    clamp_llm_request_timeout_sec,
    clamp_llm_wall_clock_budget_sec,
)
from afkbot.services.llm.reasoning import ReasoningEffort, ThinkingLevel, normalize_thinking_level

PlanningMode = Literal["off", "plan_only"]
ToolAccessMode = Literal["default", "read_only", "none"]

READ_ONLY_TOOL_NAMES = frozenset(
    {
        "app.list",
        "credentials.list",
        "debug.echo",
        "file.list",
        "file.read",
        "file.search",
        "memory.search",
        "mcp.tools.list",
        "skill.marketplace.list",
        "skill.profile.get",
        "skill.profile.list",
        "subagent.profile.get",
        "subagent.profile.list",
        "web.fetch",
        "web.search",
    }
)

_PLAN_PROMPT_OVERLAY = """# Planning Mode
You are in plan-only mode.

Requirements:
- produce a concise implementation plan before any execution;
- do not claim files were changed or work was completed;
- use only read-only inspection tools when needed;
- keep the plan actionable: goal, steps, risks, and first execution step;
- prefer 4-8 steps unless the task is tiny.
"""


@dataclass(frozen=True, slots=True)
class TurnThinkingConfig:
    """Resolved reasoning budget and safety mode for one turn."""

    thinking_level: ThinkingLevel
    reasoning_effort: ReasoningEffort | None
    max_iterations: int
    request_timeout_sec: float
    wall_clock_budget_sec: float
    tool_access_mode: ToolAccessMode
    prompt_overlay: str | None = None


def normalize_planning_mode(value: str | None) -> PlanningMode | None:
    """Normalize planning mode input."""

    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if not normalized:
        return None
    if normalized not in {"off", "plan_only"}:
        raise ValueError("planning_mode must be one of: off, plan_only")
    return normalized  # type: ignore[return-value]


def normalize_tool_access_mode(value: str | None) -> ToolAccessMode | None:
    """Normalize tool-access mode input."""

    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_")
    if not normalized:
        return None
    if normalized not in {"default", "read_only", "none"}:
        raise ValueError("tool_access_mode must be one of: default, read_only, none")
    return normalized  # type: ignore[return-value]


def resolve_turn_thinking_config(
    *,
    runtime_limit: int,
    base_timeout_sec: float,
    default_thinking_level: ThinkingLevel,
    override_thinking_level: ThinkingLevel | None,
    planning_mode: PlanningMode,
    override_tool_access_mode: ToolAccessMode | None,
    execution_budget_low_sec: float | None = None,
    execution_budget_medium_sec: float | None = None,
    execution_budget_high_sec: float | None = None,
    execution_budget_very_high_sec: float | None = None,
) -> TurnThinkingConfig:
    """Resolve effective LLM budget and safe tool surface for one turn."""

    level = override_thinking_level or default_thinking_level
    base_config = _config_for_level(level)
    max_iterations = max(1, min(runtime_limit, base_config.max_iterations))
    explicit_budget_sec = _resolve_explicit_execution_budget(
        level=level,
        low_sec=execution_budget_low_sec,
        medium_sec=execution_budget_medium_sec,
        high_sec=execution_budget_high_sec,
        very_high_sec=execution_budget_very_high_sec,
    )
    request_timeout_sec = _resolve_request_timeout_sec(
        base_timeout_sec=base_timeout_sec,
        timeout_multiplier=base_config.timeout_multiplier,
        explicit_budget_sec=explicit_budget_sec,
    )
    derived_wall_clock_budget_sec = max(
        request_timeout_sec,
        request_timeout_sec * max_iterations * base_config.wall_clock_multiplier,
    )
    wall_clock_budget_sec = derived_wall_clock_budget_sec
    if explicit_budget_sec is not None:
        wall_clock_budget_sec = min(
            derived_wall_clock_budget_sec,
            clamp_llm_wall_clock_budget_sec(explicit_budget_sec),
        )
    tool_access_mode = override_tool_access_mode or "default"
    prompt_overlay: str | None = None

    if planning_mode == "plan_only":
        max_iterations = min(max_iterations, 2)
        request_timeout_sec = clamp_llm_request_timeout_sec(max(request_timeout_sec, 45.0))
        wall_clock_budget_sec = max(
            wall_clock_budget_sec,
            request_timeout_sec * max_iterations * 1.5,
        )
        if override_tool_access_mode is None:
            tool_access_mode = "read_only"
        prompt_overlay = _PLAN_PROMPT_OVERLAY

    wall_clock_budget_sec = clamp_llm_wall_clock_budget_sec(wall_clock_budget_sec)

    return TurnThinkingConfig(
        thinking_level=level,
        reasoning_effort=base_config.reasoning_effort,
        max_iterations=max_iterations,
        request_timeout_sec=request_timeout_sec,
        wall_clock_budget_sec=wall_clock_budget_sec,
        tool_access_mode=tool_access_mode,
        prompt_overlay=prompt_overlay,
    )


def combine_prompt_overlays(*parts: str | None) -> str | None:
    """Join prompt overlays while skipping empty fragments."""

    normalized = [part.strip() for part in parts if isinstance(part, str) and part.strip()]
    if not normalized:
        return None
    return "\n\n".join(normalized)


@dataclass(frozen=True, slots=True)
class _ThinkingPreset:
    max_iterations: int
    timeout_multiplier: float
    wall_clock_multiplier: float
    reasoning_effort: ReasoningEffort | None


def _config_for_level(level: ThinkingLevel) -> _ThinkingPreset:
    if level == "low":
        return _ThinkingPreset(
            max_iterations=DEFAULT_LLM_MAX_ITERATIONS,
            timeout_multiplier=0.75,
            wall_clock_multiplier=1.5,
            reasoning_effort="low",
        )
    if level == "medium":
        return _ThinkingPreset(
            max_iterations=DEFAULT_LLM_MAX_ITERATIONS,
            timeout_multiplier=1.0,
            wall_clock_multiplier=2.0,
            reasoning_effort="medium",
        )
    if level == "high":
        return _ThinkingPreset(
            max_iterations=DEFAULT_LLM_MAX_ITERATIONS,
            timeout_multiplier=1.5,
            wall_clock_multiplier=3.0,
            reasoning_effort="high",
        )
    return _ThinkingPreset(
        max_iterations=DEFAULT_LLM_MAX_ITERATIONS,
        timeout_multiplier=2.0,
        wall_clock_multiplier=4.0,
        reasoning_effort="high",
    )


def _resolve_explicit_execution_budget(
    *,
    level: ThinkingLevel,
    low_sec: float | None,
    medium_sec: float | None,
    high_sec: float | None,
    very_high_sec: float | None,
) -> float | None:
    raw_map = {
        "low": low_sec,
        "medium": medium_sec,
        "high": high_sec,
        "very_high": very_high_sec,
    }
    selected = raw_map.get(level)
    if selected is None:
        return None
    return max(0.01, float(selected))


def _resolve_request_timeout_sec(
    *,
    base_timeout_sec: float,
    timeout_multiplier: float,
    explicit_budget_sec: float | None,
) -> float:
    """Resolve one-request timeout without exceeding the selected total task budget."""

    request_timeout_sec = clamp_llm_request_timeout_sec(float(base_timeout_sec) * timeout_multiplier)
    if explicit_budget_sec is None:
        return request_timeout_sec
    return min(
        request_timeout_sec,
        clamp_llm_request_timeout_sec(explicit_budget_sec),
    )


__all__ = [
    "PlanningMode",
    "READ_ONLY_TOOL_NAMES",
    "ToolAccessMode",
    "TurnThinkingConfig",
    "combine_prompt_overlays",
    "normalize_planning_mode",
    "normalize_thinking_level",
    "normalize_tool_access_mode",
    "resolve_turn_thinking_config",
]
