"""Shared actor resolution for Task Flow tool plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from afkbot.services.automations.principals import build_automation_principal_ref
from afkbot.services.task_flow.ai_executors import (
    EMPLOYEE_OWNER_TYPE,
    parse_employee_owner_ref,
)
from afkbot.services.tools.base import ToolContext, ToolResult
from afkbot.settings import Settings

if TYPE_CHECKING:
    from afkbot.services.task_flow.contracts import TaskFlowMetadata, TaskMetadata

_TRUSTED_TASKFLOW_DETACHED_KEY = "taskflow_detached_runtime"


@dataclass(frozen=True, slots=True)
class TaskToolActorIdentity:
    """Effective actor identity used by one task tool invocation."""

    actor_type: str
    actor_ref: str
    actor_session_id: str | None


def resolve_task_tool_actor(ctx: ToolContext) -> TaskToolActorIdentity:
    """Return the canonical actor identity for task tools in the current runtime."""

    automation_graph = None
    if isinstance(ctx.runtime_metadata, dict):
        candidate = ctx.runtime_metadata.get("automation_graph")
        if isinstance(candidate, dict):
            automation_graph = candidate

    if isinstance(automation_graph, dict):
        automation_id = automation_graph.get("automation_id")
        if isinstance(automation_id, int):
            return TaskToolActorIdentity(
                actor_type="automation",
                actor_ref=build_automation_principal_ref(
                    profile_id=ctx.profile_id,
                    automation_id=automation_id,
                ),
                actor_session_id=None,
            )

    detached_actor = _trusted_taskflow_detached_actor(ctx=ctx)
    if detached_actor is not None:
        return detached_actor

    return TaskToolActorIdentity(actor_type="human", actor_ref="web-user", actor_session_id=ctx.session_id)


async def restrict_employee_read_owner_scope(
    *,
    ctx: ToolContext,
    settings: Settings,
    target_profile_id: str,
    owner_type: str | None,
    owner_ref: str | None,
) -> tuple[str | None, str | None, ToolResult | None]:
    """Restrict employee runtime read filters to self or managed reports."""

    actor = resolve_task_tool_actor(ctx)
    if actor.actor_type != EMPLOYEE_OWNER_TYPE:
        return owner_type, owner_ref, None

    if target_profile_id != ctx.profile_id:
        return None, None, ToolResult.error(
            error_code="task_scope_forbidden",
            reason="Employee Task Flow reads are restricted to the active profile.",
        )

    if owner_type is None and owner_ref is None:
        return EMPLOYEE_OWNER_TYPE, actor.actor_ref, None

    if owner_type != EMPLOYEE_OWNER_TYPE or not owner_ref:
        return None, None, ToolResult.error(
            error_code="task_employee_scope_forbidden",
            reason="Employee runtimes can read only employee-scoped Task Flow views.",
        )

    if owner_ref == actor.actor_ref:
        return owner_type, owner_ref, None

    from afkbot.services.employees import EmployeeService, EmployeeServiceError

    try:
        chart = await EmployeeService(settings).build_org_chart(profile_id=target_profile_id)
    except EmployeeServiceError as exc:
        return None, None, ToolResult.error(error_code=exc.error_code, reason=exc.reason)

    employee = chart.employees.get(actor.actor_ref)
    readable_employee_ids = (
        set(employee.reports) | set(employee.derived_reports) | set(employee.can_delegate_to)
        if employee is not None
        else set()
    )
    if owner_ref in readable_employee_ids:
        return owner_type, owner_ref, None

    return None, None, ToolResult.error(
        error_code="task_employee_scope_forbidden",
        reason="Employee runtimes can read only their own or managed employees' Task Flow views.",
    )


async def ensure_employee_task_read_scope(
    *,
    ctx: ToolContext,
    settings: Settings,
    target_profile_id: str,
    task: "TaskMetadata",
) -> ToolResult | None:
    """Restrict direct employee task reads to the current task or managed employee scope."""

    actor = resolve_task_tool_actor(ctx)
    if actor.actor_type != EMPLOYEE_OWNER_TYPE:
        return None

    if target_profile_id != ctx.profile_id:
        return ToolResult.error(
            error_code="task_scope_forbidden",
            reason="Employee Task Flow reads are restricted to the active profile.",
        )

    from afkbot.services.tools.plugins.task_scope import runtime_task_id

    if task.id == runtime_task_id(ctx=ctx):
        return None

    readable_employee_ids, error = await _employee_readable_employee_ids(
        settings=settings,
        profile_id=target_profile_id,
        employee_id=actor.actor_ref,
    )
    if error is not None:
        return error

    if _task_references_readable_employee(task=task, employee_ids=readable_employee_ids):
        return None

    return ToolResult.error(
        error_code="task_employee_scope_forbidden",
        reason="Employee runtimes can read only tasks for themselves or managed employees.",
    )


async def ensure_employee_flow_read_scope(
    *,
    ctx: ToolContext,
    settings: Settings,
    target_profile_id: str,
    flow: "TaskFlowMetadata",
) -> ToolResult | None:
    """Restrict direct employee flow document reads to managed employee scope."""

    actor = resolve_task_tool_actor(ctx)
    if actor.actor_type != EMPLOYEE_OWNER_TYPE:
        return None

    if target_profile_id != ctx.profile_id:
        return ToolResult.error(
            error_code="task_scope_forbidden",
            reason="Employee Task Flow reads are restricted to the active profile.",
        )

    readable_employee_ids, error = await _employee_readable_employee_ids(
        settings=settings,
        profile_id=target_profile_id,
        employee_id=actor.actor_ref,
    )
    if error is not None:
        return error

    if flow.default_owner_type == EMPLOYEE_OWNER_TYPE and flow.default_owner_ref in readable_employee_ids:
        return None
    if flow.created_by_type == EMPLOYEE_OWNER_TYPE and flow.created_by_ref in readable_employee_ids:
        return None

    from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service

    try:
        flow_tasks = await get_task_flow_service(settings).list_tasks(
            profile_id=target_profile_id,
            flow_id=flow.id,
        )
    except TaskFlowServiceError as exc:
        return ToolResult.error(error_code=exc.error_code, reason=exc.reason)

    if any(
        _task_references_readable_employee(task=task, employee_ids=readable_employee_ids)
        for task in flow_tasks
    ):
        return None

    return ToolResult.error(
        error_code="task_employee_scope_forbidden",
        reason="Employee runtimes can read only flow documents for their own or managed work.",
    )


def _trusted_taskflow_detached_actor(*, ctx: ToolContext) -> TaskToolActorIdentity | None:
    """Resolve employee actor only from trusted detached-runtime context."""

    trusted_context = ctx.trusted_runtime_context
    if not isinstance(trusted_context, dict):
        return None
    detached_payload = trusted_context.get(_TRUSTED_TASKFLOW_DETACHED_KEY)
    if not isinstance(detached_payload, dict):
        return None

    owner_type = str(detached_payload.get("owner_type") or "").strip().lower()
    owner_ref = str(detached_payload.get("owner_ref") or "").strip()
    if owner_type != EMPLOYEE_OWNER_TYPE or not owner_ref:
        return None

    employee_id = parse_employee_owner_ref(owner_ref)
    if employee_id is None:
        return None

    return TaskToolActorIdentity(
        actor_type=EMPLOYEE_OWNER_TYPE,
        actor_ref=employee_id,
        actor_session_id=ctx.session_id,
    )


async def _employee_readable_employee_ids(
    *,
    settings: Settings,
    profile_id: str,
    employee_id: str,
) -> tuple[set[str], ToolResult | None]:
    from afkbot.services.employees import EmployeeService, EmployeeServiceError

    try:
        chart = await EmployeeService(settings).build_org_chart(profile_id=profile_id)
    except EmployeeServiceError as exc:
        return set(), ToolResult.error(error_code=exc.error_code, reason=exc.reason)

    employee = chart.employees.get(employee_id)
    if employee is None or employee.status != "active":
        return set(), ToolResult.error(
            error_code="task_employee_scope_forbidden",
            reason="Employee runtime actor is not active in this profile.",
        )

    return {
        employee_id,
        *employee.reports,
        *employee.derived_reports,
        *employee.can_delegate_to,
    }, None


def _task_references_readable_employee(
    *,
    task: "TaskMetadata",
    employee_ids: set[str],
) -> bool:
    return any(
        principal_type == EMPLOYEE_OWNER_TYPE and principal_ref in employee_ids
        for principal_type, principal_ref in (
            (task.owner_type, task.owner_ref),
            (task.reviewer_type, task.reviewer_ref),
            (task.created_by_type, task.created_by_ref),
        )
    )
