"""Planning/debug payload helpers for one prepared turn."""

from __future__ import annotations

from afkbot.services.agent_loop.parallel_planning import build_parallel_strategy_payload
from afkbot.services.agent_loop.execution_posture import first_execution_blocker
from afkbot.services.agent_loop.skill_router import SkillRoute
from afkbot.services.llm.contracts import LLMToolDefinition
from afkbot.services.tools.base import ToolCall, ToolResult


def planned_tools_final_message(
    *,
    user_message: str,
    tool_calls: list[ToolCall],
    tool_results: list[ToolResult],
) -> str:
    """Build deterministic final text for bridge flow with preplanned tool calls."""

    if not tool_results:
        return (
            "No tool calls were executed. "
            "The request was not completed."
        )
    blocked = first_execution_blocker(
        tool_calls=tool_calls,
        tool_results=tool_results,
    )
    if blocked is not None:
        return blocked.message
    failed = [result for result in tool_results if not result.ok]
    if not failed:
        return f"Completed requested operations for: {user_message}"

    first = failed[0]
    error_code = (first.error_code or "tool_failed").strip()
    reason = (first.reason or "").strip()
    details = f"{error_code}: {reason}" if reason else error_code
    return (
        "One or more requested operations failed. "
        f"First error: {details}"
    )


def turn_plan_payload(
    *,
    machine_state: str,
    skill_route: SkillRoute,
    explicit_skill_mentions: set[str],
    explicit_enforceable_skill_mentions: set[str],
    explicit_subagent_mentions: set[str],
    available_tools: tuple[LLMToolDefinition, ...],
    planned_tool_calls: list[ToolCall] | None,
    planning_mode: str,
    chat_planning_mode: str | None,
    execution_planning_enabled: bool,
    thinking_level: str,
    tool_access_mode: str,
    request_timeout_sec: float | None = None,
    wall_clock_budget_sec: float | None = None,
) -> dict[str, object]:
    """Build deterministic planning payload with skill-first debug metadata."""

    payload: dict[str, object] = {
        "state": machine_state,
        "explicit_skill_mentions": sorted(explicit_skill_mentions),
        "explicit_skill_mentions_enforceable": sorted(explicit_enforceable_skill_mentions),
        "selected_skill_names": list(skill_route.selected_skill_names),
        "inferred_skill_names": list(skill_route.inferred_skill_names),
        "explicit_subagent_mentions": sorted(explicit_subagent_mentions),
        "planning_mode": planning_mode,
        "chat_planning_mode": chat_planning_mode or "off",
        "execution_planning_enabled": execution_planning_enabled,
        "thinking_level": thinking_level,
        "tool_access_mode": tool_access_mode,
    }
    if request_timeout_sec is not None:
        payload["request_timeout_sec"] = round(float(request_timeout_sec), 3)
    if wall_clock_budget_sec is not None:
        payload["wall_clock_budget_sec"] = round(float(wall_clock_budget_sec), 3)
    if available_tools:
        payload["available_tools_after_filter"] = [tool.name for tool in available_tools]
    if planned_tool_calls:
        payload["planned_tool_names"] = [call.name for call in planned_tool_calls]
    parallel_strategy = build_parallel_strategy_payload(
        available_tools=available_tools,
        planned_tool_calls=planned_tool_calls,
    )
    if parallel_strategy:
        payload["parallel_strategy"] = parallel_strategy
    return payload
