"""Shared parallel-planning metadata and rendering helpers."""

from __future__ import annotations

from collections.abc import Iterable

from afkbot.services.llm.contracts import LLMToolDefinition
from afkbot.services.tools.base import ToolCall

PARALLEL_SAFE_FILE_TOOL_NAMES = frozenset({"file.list", "file.read", "file.search"})
SESSION_JOB_NESTED_TOOL_NAMES = frozenset({"bash.exec", "subagent.run"})

PARALLEL_HINT_GROUP_FILE_TOOLS = "group_parallel_safe_file_tools"
PARALLEL_HINT_PREFER_SESSION_JOB_RUN = "prefer_session_job_run_for_independent_jobs"
PARALLEL_HINT_AVOID_REDUNDANT_DISCOVERY = "avoid_redundant_discovery"

PARALLEL_EXECUTION_MODE_FILE_TOOLS = "parallel_tool_calls"
PARALLEL_EXECUTION_MODE_SESSION_JOBS = "session_job_run"
PARALLEL_EXECUTION_MODE_SERIAL_FANOUT_CANDIDATE = "serial_fanout_candidate"


def parallel_strategy_hints(*, available_tool_names: Iterable[str]) -> tuple[str, ...]:
    """Return deterministic parallelization hints for the visible tool surface."""

    normalized_names = {str(name).strip() for name in available_tool_names if str(name).strip()}
    hints: list[str] = []
    if normalized_names & PARALLEL_SAFE_FILE_TOOL_NAMES:
        hints.append(PARALLEL_HINT_GROUP_FILE_TOOLS)
    if "session.job.run" in normalized_names:
        hints.append(PARALLEL_HINT_PREFER_SESSION_JOB_RUN)
    if normalized_names & (PARALLEL_SAFE_FILE_TOOL_NAMES | {"session.job.run"}):
        hints.append(PARALLEL_HINT_AVOID_REDUNDANT_DISCOVERY)
    return tuple(hints)


def build_parallel_strategy_payload(
    *,
    available_tools: tuple[LLMToolDefinition, ...],
    planned_tool_calls: list[ToolCall] | None,
) -> dict[str, object]:
    """Describe parallelizable execution structure for plan/debug consumers."""

    available_tool_names = [tool.name for tool in available_tools if tool.name.strip()]
    payload: dict[str, object] = {}
    hints = parallel_strategy_hints(available_tool_names=available_tool_names)
    if hints:
        payload["hints"] = list(hints)
    if not planned_tool_calls:
        return payload

    planned_names = [call.name for call in planned_tool_calls if call.name.strip()]
    if len(planned_tool_calls) >= 2 and all(
        name in PARALLEL_SAFE_FILE_TOOL_NAMES for name in planned_names
    ):
        payload["execution_mode"] = PARALLEL_EXECUTION_MODE_FILE_TOOLS
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
            payload["execution_mode"] = PARALLEL_EXECUTION_MODE_SESSION_JOBS
            payload["session_job_count"] = len(jobs)
            if kinds:
                payload["session_job_kinds"] = kinds
        return payload

    if len(planned_tool_calls) >= 2 and all(
        name in SESSION_JOB_NESTED_TOOL_NAMES for name in planned_names
    ):
        payload["execution_mode"] = PARALLEL_EXECUTION_MODE_SERIAL_FANOUT_CANDIDATE
        payload["fanout_tool_names"] = planned_names
        payload["preferred_batch_tool"] = "session.job.run"
    return payload


def build_parallel_strategy_note(
    *,
    known_tool_names: Iterable[str],
    planning_mode: str,
) -> str | None:
    """Render trusted prompt guidance for batching independent work."""

    normalized_names = {str(name).strip() for name in known_tool_names if str(name).strip()}
    lines: list[str] = []
    if normalized_names & PARALLEL_SAFE_FILE_TOOL_NAMES:
        lines.append(
            "- Prefer first-class file tools for repository inspection. Use shell wrappers "
            "such as `find`, `ls`, or ad-hoc Python directory listing only when `file.*` "
            "tools cannot provide the needed data."
        )
        lines.append(
            "- When several independent file reads, lists, or searches are needed, emit all "
            "of those `file.*` tool calls in the same assistant response instead of probing "
            "one-by-one."
        )
    if "session.job.run" in normalized_names:
        lines.append(
            "- When two or more independent shell or subagent jobs can start immediately and "
            "you need every result before continuing, prefer one `session.job.run` call over "
            "multiple separate `bash.exec` or `subagent.run` calls."
        )
    if normalized_names & (PARALLEL_SAFE_FILE_TOOL_NAMES | SESSION_JOB_NESTED_TOOL_NAMES):
        lines.append(
            "- Avoid redundant discovery. Do not repeat equivalent inspection with multiple "
            "tools after one result already answered the question."
        )
    if not lines:
        return None
    intro = (
        "Plan with the later execution surface in mind."
        if planning_mode == "plan_only"
        else "Choose the minimal grouped tool strategy that gathers evidence quickly."
    )
    return "# Parallel and Tool Strategy\n" + intro + "\n" + "\n".join(lines)


def render_parallel_strategy_progress_detail(parallel_strategy: object) -> str | None:
    """Render one compact progress fragment for plan-time parallel strategy."""

    if not isinstance(parallel_strategy, dict):
        return None
    execution_mode = str(parallel_strategy.get("execution_mode") or "").strip()
    hint_names_raw = parallel_strategy.get("hints")
    if execution_mode == PARALLEL_EXECUTION_MODE_SESSION_JOBS:
        return "parallel=session-jobs"
    if execution_mode == PARALLEL_EXECUTION_MODE_FILE_TOOLS:
        return "parallel=file-tools"
    if execution_mode == PARALLEL_EXECUTION_MODE_SERIAL_FANOUT_CANDIDATE:
        return "parallel_hint=session-jobs"
    if not isinstance(hint_names_raw, list):
        return None
    hint_names = {str(item).strip() for item in hint_names_raw if str(item).strip()}
    hint_parts: list[str] = []
    if PARALLEL_HINT_GROUP_FILE_TOOLS in hint_names:
        hint_parts.append("file-tools")
    if PARALLEL_HINT_PREFER_SESSION_JOB_RUN in hint_names:
        hint_parts.append("session-jobs")
    if not hint_parts:
        return None
    return f"parallel_hint={'+'.join(hint_parts)}"
