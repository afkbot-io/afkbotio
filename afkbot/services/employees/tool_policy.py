"""Hard tool gates for Task Flow employee runtimes."""

from __future__ import annotations

from collections.abc import Mapping

from afkbot.services.employees import EmployeeService, EmployeeServiceError
from afkbot.services.task_flow.ai_executors import EMPLOYEE_OWNER_TYPE, parse_employee_owner_ref
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings

_DETACHED_CONTEXT_KEY = "taskflow_detached_runtime"
_SUBAGENT_RUN_TOOL = "subagent.run"
_SESSION_JOB_RUN_TOOL = "session.job.run"


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

    requested_subagents = _requested_subagent_names(tool_name=tool_name, params=params)
    if not requested_subagents:
        return None
    if not employee.can_use_subagents:
        return ToolResult.error(
            error_code="employee_tool_forbidden",
            reason=f"Employee {employee.id} is not allowed to run subagents",
        )
    if employee.subagent_allowlist:
        forbidden = tuple(
            name for name in requested_subagents if name not in employee.subagent_allowlist
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


def _requested_subagent_names(*, tool_name: str, params: Mapping[str, object]) -> tuple[str, ...]:
    if tool_name == _SUBAGENT_RUN_TOOL:
        subagent_name = str(params.get("subagent_name") or "").strip()
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
        subagent_name = str(item.get("subagent_name") or "").strip()
        if subagent_name:
            names.append(subagent_name)
    return tuple(names)
