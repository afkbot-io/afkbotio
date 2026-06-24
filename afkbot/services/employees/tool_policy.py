"""Hard tool gates for Task Flow employee runtimes."""

from __future__ import annotations

from collections.abc import Mapping

from afkbot.services.employees import EmployeeService, EmployeeServiceError
from afkbot.services.task_flow_principals import EMPLOYEE_OWNER_TYPE, parse_employee_owner_ref
from afkbot.services.task_flow.work_modes import (
    KNOWLEDGE_MAINTENANCE_WORK_MODE,
    MANAGER_INTAKE_WORK_MODE,
)
from afkbot.services.naming import normalize_runtime_name
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings

_DETACHED_CONTEXT_KEY = "taskflow_detached_runtime"
_SUBAGENT_RUN_TOOL = "subagent.run"
_SESSION_JOB_RUN_TOOL = "session.job.run"
_KNOWLEDGE_MAINTENANCE_ALLOWED_TOOLS = ("task.*",)
_MANAGER_INTAKE_ALLOWED_TOOLS = (
    "memory.*",
    "task.get",
    "task.list",
    "task.board",
    "task.feed.list",
    "task.review.list",
    "task.context.get",
    "task.event.list",
    "task.run.get",
    "task.run.list",
    "task.comment.add",
    "task.comment.list",
    "task.doc.list",
    "task.doc.put",
    "task.doc.confirm",
    "task.delegate",
    "task.update",
    "task.block",
    "task.dependency.add",
    "task.dependency.remove",
    "task.dependency.list",
    "task.flow.get",
    "task.flow.list",
)


async def employee_tool_policy_result(
    *,
    settings: Settings,
    profile_id: str,
    trusted_runtime_context: Mapping[str, object] | None,
    tool_name: str,
    params: Mapping[str, object],
) -> ToolResult | None:
    """Return a tool error when an employee runtime is not allowed to call a tool."""

    employee_id = _employee_id_from_trusted_context(trusted_runtime_context)
    if employee_id is None:
        return None
    try:
        employee = await EmployeeService(settings).get_employee(
            profile_id=profile_id,
            employee_id=employee_id,
        )
    except EmployeeServiceError as exc:
        return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
    if employee.status != "active":
        return ToolResult.error(
            error_code="employee_tool_forbidden",
            reason=f"Employee {employee.id} is {employee.status} and cannot call tools",
        )

    if not employee.allowed_tools:
        return ToolResult.error(
            error_code="employee_tool_forbidden",
            reason=f"Employee {employee.id} has no allowed tools configured",
        )

    if not _tool_name_allowed(
        tool_name=tool_name,
        allowed_tools=employee.allowed_tools,
    ):
        return ToolResult.error(
            error_code="employee_tool_forbidden",
            reason=f"Employee {employee.id} is not allowed to call {tool_name}",
        )
    work_mode = _work_mode_from_trusted_context(trusted_runtime_context)
    if work_mode == KNOWLEDGE_MAINTENANCE_WORK_MODE:
        if not _tool_name_allowed(
            tool_name=tool_name,
            allowed_tools=_KNOWLEDGE_MAINTENANCE_ALLOWED_TOOLS,
        ):
            return ToolResult.error(
                error_code="employee_tool_forbidden",
                reason=(
                    "Task Flow knowledge maintenance mode may call only task.* tools. "
                    "Delegate implementation work instead of using execution tools here."
                ),
            )
    if work_mode == MANAGER_INTAKE_WORK_MODE:
        if not _tool_name_allowed(
            tool_name=tool_name,
            allowed_tools=_MANAGER_INTAKE_ALLOWED_TOOLS,
        ):
            return ToolResult.error(
                error_code="employee_tool_forbidden",
                reason=(
                    "Task Flow manager intake mode may call only coordination task tools "
                    "and memory.* tools. Delegate specialist execution and review actions "
                    "instead of performing them here."
                ),
            )

    requested_subagents = _requested_subagent_names(tool_name=tool_name, params=params)
    if not requested_subagents:
        return None
    if not employee.can_use_subagents:
        return ToolResult.error(
            error_code="employee_tool_forbidden",
            reason=f"Employee {employee.id} is not allowed to run subagents",
        )
    if employee.subagent_allowlist:
        allowed_subagents = {
            _normalize_subagent_name(name) for name in employee.subagent_allowlist
        }
        forbidden = tuple(
            name for name in requested_subagents if name not in allowed_subagents
        )
        if forbidden:
            return ToolResult.error(
                error_code="employee_tool_forbidden",
                reason=f"Employee {employee.id} is not allowed to run subagent {forbidden[0]}",
            )
    return None


def _tool_name_allowed(*, tool_name: str, allowed_tools: tuple[str, ...]) -> bool:
    normalized_tool_name = str(tool_name or "").strip()
    for item in allowed_tools:
        allowed = str(item or "").strip()
        if not allowed:
            continue
        if allowed == "*":
            return True
        if allowed == normalized_tool_name:
            return True
        if allowed.endswith(".*") and normalized_tool_name.startswith(allowed[:-1]):
            return True
    return False


def _employee_id_from_trusted_context(
    trusted_runtime_context: Mapping[str, object] | None,
) -> str | None:
    if not isinstance(trusted_runtime_context, Mapping):
        return None
    payload = trusted_runtime_context.get(_DETACHED_CONTEXT_KEY)
    if not isinstance(payload, Mapping):
        return None
    owner_type = str(payload.get("owner_type") or "").strip().lower()
    owner_ref = str(payload.get("owner_ref") or "").strip()
    if owner_type != EMPLOYEE_OWNER_TYPE:
        return None
    return parse_employee_owner_ref(owner_ref)


def _work_mode_from_trusted_context(
    trusted_runtime_context: Mapping[str, object] | None,
) -> str | None:
    if not isinstance(trusted_runtime_context, Mapping):
        return None
    payload = trusted_runtime_context.get(_DETACHED_CONTEXT_KEY)
    if not isinstance(payload, Mapping):
        return None
    normalized = str(payload.get("work_mode") or "").strip().lower()
    return normalized or None


def _requested_subagent_names(*, tool_name: str, params: Mapping[str, object]) -> tuple[str, ...]:
    if tool_name == _SUBAGENT_RUN_TOOL:
        subagent_name = _normalize_subagent_name(str(params.get("subagent_name") or ""))
        return (subagent_name,) if subagent_name else ()
    if tool_name != _SESSION_JOB_RUN_TOOL:
        return ()
    jobs = params.get("jobs")
    if not isinstance(jobs, list):
        return ()
    names: list[str] = []
    for item in jobs:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("kind") or "").strip() != "subagent":
            continue
        subagent_name = _normalize_subagent_name(str(item.get("subagent_name") or ""))
        if subagent_name:
            names.append(subagent_name)
    return tuple(names)


def _normalize_subagent_name(raw_name: str) -> str:
    raw = str(raw_name or "").strip()
    if not raw:
        return ""
    try:
        return normalize_runtime_name(raw)
    except ValueError:
        return raw
