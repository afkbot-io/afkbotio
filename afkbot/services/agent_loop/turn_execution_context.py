"""Resolution helpers for turn-level planning/thinking execution context."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.planning_policy import (
    ChatPlanningMode,
    execution_planning_prompt_overlay,
    should_enable_execution_planning,
)
from afkbot.services.agent_loop.thinking import TurnThinkingConfig, combine_prompt_overlays, resolve_turn_thinking_config
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.llm.reasoning import ThinkingLevel
from afkbot.services.policy import PolicyEngine


@dataclass(frozen=True, slots=True)
class ResolvedTurnExecutionContext:
    """Resolved planning and tool-surface settings for one turn."""

    thinking_config: TurnThinkingConfig
    execution_planning_mode: ChatPlanningMode
    execution_planning_enabled: bool
    effective_overrides: TurnContextOverrides


def resolve_turn_execution_context(
    *,
    policy: ProfilePolicy,
    policy_engine: PolicyEngine,
    runtime_limit: int,
    base_timeout_sec: float,
    default_thinking_level: ThinkingLevel,
    chat_planning_mode: ChatPlanningMode,
    execution_budget_low_sec: float,
    execution_budget_medium_sec: float,
    execution_budget_high_sec: float,
    execution_budget_very_high_sec: float,
    raw_user_message: str,
    context_overrides: TurnContextOverrides | None,
) -> ResolvedTurnExecutionContext:
    """Resolve safe planning/thinking/tool overrides for the current turn."""

    thinking_config = resolve_turn_thinking_config(
        runtime_limit=policy_engine.effective_main_iterations(
            policy=policy,
            runtime_limit=runtime_limit,
        ),
        base_timeout_sec=base_timeout_sec,
        default_thinking_level=default_thinking_level,
        override_thinking_level=(
            None if context_overrides is None else context_overrides.thinking_level
        ),
        planning_mode=(
            "off" if context_overrides is None else context_overrides.planning_mode
        ),
        override_tool_access_mode=(
            None if context_overrides is None else context_overrides.tool_access_mode
        ),
        execution_budget_low_sec=execution_budget_low_sec,
        execution_budget_medium_sec=execution_budget_medium_sec,
        execution_budget_high_sec=execution_budget_high_sec,
        execution_budget_very_high_sec=execution_budget_very_high_sec,
    )
    execution_planning_mode = chat_planning_mode
    if (
        context_overrides is not None
        and context_overrides.execution_planning_mode is not None
    ):
        execution_planning_mode = context_overrides.execution_planning_mode
    effective_overrides_planning_mode = (
        "off" if context_overrides is None else context_overrides.planning_mode
    )
    execution_planning_enabled = (
        effective_overrides_planning_mode != "plan_only"
        and should_enable_execution_planning(
            message=raw_user_message,
            planning_mode=execution_planning_mode,
        )
    )
    effective_runtime_metadata = (
        None
        if context_overrides is None or not context_overrides.runtime_metadata
        else context_overrides.runtime_metadata
    )
    effective_overrides = TurnContextOverrides(
        runtime_metadata=effective_runtime_metadata,
        prompt_overlay=combine_prompt_overlays(
            None if context_overrides is None else context_overrides.prompt_overlay,
            execution_planning_prompt_overlay() if execution_planning_enabled else None,
            thinking_config.prompt_overlay,
        ),
        planning_mode=effective_overrides_planning_mode,
        execution_planning_mode=execution_planning_mode,
        thinking_level=thinking_config.thinking_level,
        tool_access_mode=thinking_config.tool_access_mode,
    )
    return ResolvedTurnExecutionContext(
        thinking_config=thinking_config,
        execution_planning_mode=execution_planning_mode,
        execution_planning_enabled=execution_planning_enabled,
        effective_overrides=effective_overrides,
    )
