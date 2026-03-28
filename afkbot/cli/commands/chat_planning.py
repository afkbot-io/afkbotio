"""Plan-first chat helpers for CLI flows."""

from __future__ import annotations

from afkbot.services.agent_loop.planning_policy import (
    ChatPlanningMode,
    is_explicit_plan_request,
    normalize_chat_planning_mode,
    should_offer_plan,
)
from afkbot.services.llm.reasoning import ThinkingLevel, normalize_thinking_level
from afkbot.services.chat_session.turn_planning import (
    build_execution_overrides_from_plan,
    build_plan_only_overrides,
)

__all__ = [
    "ChatPlanningMode",
    "build_execution_overrides_from_plan",
    "build_plan_only_overrides",
    "is_explicit_plan_request",
    "normalize_chat_planning_mode",
    "resolve_cli_thinking_level",
    "should_offer_plan",
]


def resolve_cli_thinking_level(
    *,
    explicit_value: str | None,
    default_value: str,
) -> ThinkingLevel | None:
    """Resolve effective CLI thinking level from explicit flag or settings default."""

    return normalize_thinking_level(explicit_value or default_value)
