"""Planning/debug payload helpers for one prepared turn."""

from __future__ import annotations

from afkbot.services.agent_loop.execution_posture import first_execution_blocker
from afkbot.services.agent_loop.skill_router import SkillRoute
from afkbot.services.llm.contracts import LLMToolDefinition
from afkbot.services.tools.base import ToolCall, ToolResult

_PARALLEL_SAFE_FILE_TOOL_NAMES = frozenset({"file.list", "file.read", "file.search"})
_SESSION_JOB_NESTED_TOOL_NAMES = frozenset({"bash.exec", "subagent.run"})


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
    parallel_strategy = _parallel_strategy_payload(
        available_tools=available_tools,
        planned_tool_calls=planned_tool_calls,
    )
    if parallel_strategy:
        payload["parallel_strategy"] = parallel_strategy
    return payload


def _parallel_strategy_payload(
    *,
    available_tools: tuple[LLMToolDefinition, ...],
    planned_tool_calls: list[ToolCall] | None,
) -> dict[str, object]:
    """Describe parallelizable execution structure for plan/debug consumers."""

    available_tool_names = {tool.name for tool in available_tools if tool.name.strip()}
    hints: list[str] = []
    if available_tool_names & _PARALLEL_SAFE_FILE_TOOL_NAMES:
        hints.append("group_parallel_safe_file_tools")
    if "session.job.run" in available_tool_names:
        hints.append("prefer_session_job_run_for_independent_jobs")
    if available_tool_names & (_PARALLEL_SAFE_FILE_TOOL_NAMES | {"session.job.run"}):
        hints.append("avoid_redundant_discovery")

    payload: dict[str, object] = {}
    if hints:
        payload["hints"] = hints
    if not planned_tool_calls:
        return payload

    planned_names = [call.name for call in planned_tool_calls if call.name.strip()]
    if len(planned_tool_calls) >= 2 and all(name in _PARALLEL_SAFE_FILE_TOOL_NAMES for name in planned_names):
        payload["execution_mode"] = "parallel_tool_calls"
        payload["parallel_tool_names"] = planned_names
        return payload

    if len(planned_tool_calls) == 1 and planned_tool_calls[0].name == "session.job.run":
        jobs = planned_tool_calls[0].params.get("jobs")
        if isinstance(jobs, list):
            kinds = sorted(
                {
                    str(job.get("kind") or "").strip()
                    for job in jobs
                    if isinstance(job, dict) and str(job.get("kind") or "").strip()
                }
            )
            payload["execution_mode"] = "session_job_run"
            payload["session_job_count"] = len(jobs)
            if kinds:
                payload["session_job_kinds"] = kinds
        return payload

    if len(planned_tool_calls) >= 2 and all(name in _SESSION_JOB_NESTED_TOOL_NAMES for name in planned_names):
        payload["execution_mode"] = "serial_fanout_candidate"
        payload["fanout_tool_names"] = planned_names
        payload["preferred_batch_tool"] = "session.job.run"
    return payload
