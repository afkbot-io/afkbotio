"""Planning override helpers for chat session turn orchestration."""

from __future__ import annotations

from afkbot.services.agent_loop.thinking import combine_prompt_overlays
from afkbot.services.agent_loop.turn_context import TurnContextOverrides, merge_turn_context_overrides
from afkbot.services.llm.reasoning import ThinkingLevel


def build_plan_only_overrides(
    *,
    base_overrides: TurnContextOverrides | None,
    thinking_level: ThinkingLevel | None,
    persist_turn: bool = False,
) -> TurnContextOverrides:
    """Build trusted overrides for safe plan-only execution."""

    minimum_plan_level = _minimum_plan_level(thinking_level)
    merged = merge_turn_context_overrides(
        base_overrides,
        TurnContextOverrides(
            prompt_overlay=(
                "Return only the plan. Do not execute, claim completion, or describe fake edits."
            ),
            planning_mode="plan_only",
            execution_planning_mode="off",
            thinking_level=minimum_plan_level,
            tool_access_mode="read_only",
            persist_turn=persist_turn,
        ),
    )
    assert merged is not None
    return merged


def build_execution_overrides_from_plan(
    *,
    base_overrides: TurnContextOverrides | None,
    approved_plan: str,
    thinking_level: ThinkingLevel | None,
) -> TurnContextOverrides | None:
    """Attach one approved plan to the execution turn so the model can follow it."""

    plan_text = approved_plan.strip()
    if not plan_text and thinking_level is None:
        return base_overrides
    execution_overlay = None
    if plan_text:
        execution_overlay = combine_prompt_overlays(
            None,
            "# Approved Execution Plan\n"
            f"{plan_text}\n\n"
            "Follow the approved plan, but adapt if repository reality differs.",
        )
    return merge_turn_context_overrides(
        base_overrides,
        TurnContextOverrides(
            prompt_overlay=execution_overlay,
            execution_planning_mode="off",
            thinking_level=thinking_level,
        ),
    )


def _minimum_plan_level(level: ThinkingLevel | None) -> ThinkingLevel:
    if level in {"high", "very_high"}:
        return level
    return "high"
