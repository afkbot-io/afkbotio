"""Tests for structured turn planning artifacts."""

from __future__ import annotations

from afkbot.services.agent_loop.turn_planning_artifacts import turn_plan_payload
from afkbot.services.agent_loop.skill_router import SkillRoute
from afkbot.services.llm.contracts import LLMToolDefinition
from afkbot.services.tools.base import ToolCall


def _route() -> SkillRoute:
    return SkillRoute(
        selected_skill_names=(),
        executable_skill_names=(),
        advisory_skill_names=(),
        unavailable_skill_names=(),
        unavailable_blocking_skill_names=(),
        explicit_skill_names=(),
        affinity_skill_names=(),
        inferred_skill_names=(),
        tool_names=(),
        app_names=(),
        preferred_tool_order=(),
    )


def _tool(name: str) -> LLMToolDefinition:
    return LLMToolDefinition(name=name, description=name, parameters_schema={})


def test_turn_plan_payload_includes_parallel_strategy_hints_for_visible_tools() -> None:
    """Visible file tools and session.job.run should publish deterministic parallel hints."""

    payload = turn_plan_payload(
        machine_state="planning",
        skill_route=_route(),
        explicit_skill_mentions=set(),
        explicit_enforceable_skill_mentions=set(),
        explicit_subagent_mentions=set(),
        available_tools=(
            _tool("file.read"),
            _tool("file.search"),
            _tool("session.job.run"),
        ),
        planned_tool_calls=None,
        planning_mode="plan_only",
        chat_planning_mode="on",
        execution_planning_enabled=True,
        thinking_level="high",
        tool_access_mode="read_only",
    )

    assert payload["parallel_strategy"] == {
        "hints": [
            "group_parallel_safe_file_tools",
            "prefer_session_job_run_for_independent_jobs",
            "avoid_redundant_discovery",
        ]
    }


def test_turn_plan_payload_marks_parallel_safe_planned_tool_calls() -> None:
    """Preplanned parallel-safe file calls should be marked as grouped parallel work."""

    payload = turn_plan_payload(
        machine_state="planning",
        skill_route=_route(),
        explicit_skill_mentions=set(),
        explicit_enforceable_skill_mentions=set(),
        explicit_subagent_mentions=set(),
        available_tools=(
            _tool("file.read"),
            _tool("file.search"),
        ),
        planned_tool_calls=[
            ToolCall(name="file.read", params={"path": "a.txt"}),
            ToolCall(name="file.read", params={"path": "b.txt"}),
        ],
        planning_mode="off",
        chat_planning_mode="off",
        execution_planning_enabled=False,
        thinking_level="medium",
        tool_access_mode="default",
    )

    assert payload["parallel_strategy"]["execution_mode"] == "parallel_tool_calls"
    assert payload["parallel_strategy"]["parallel_tool_names"] == ["file.read", "file.read"]


def test_turn_plan_payload_marks_session_job_batch_execution() -> None:
    """Preplanned session.job.run should record job-count and job-kind structure."""

    payload = turn_plan_payload(
        machine_state="planning",
        skill_route=_route(),
        explicit_skill_mentions=set(),
        explicit_enforceable_skill_mentions=set(),
        explicit_subagent_mentions=set(),
        available_tools=(
            _tool("session.job.run"),
            _tool("bash.exec"),
            _tool("subagent.run"),
        ),
        planned_tool_calls=[
            ToolCall(
                name="session.job.run",
                params={
                    "jobs": [
                        {"kind": "bash", "cmd": "echo one"},
                        {"kind": "subagent", "prompt": "review"},
                    ]
                },
            )
        ],
        planning_mode="off",
        chat_planning_mode="off",
        execution_planning_enabled=False,
        thinking_level="medium",
        tool_access_mode="default",
    )

    assert payload["parallel_strategy"]["execution_mode"] == "session_job_run"
    assert payload["parallel_strategy"]["session_job_count"] == 2
    assert payload["parallel_strategy"]["session_job_kinds"] == ["bash", "subagent"]
