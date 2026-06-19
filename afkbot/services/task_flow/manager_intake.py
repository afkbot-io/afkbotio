"""Manager-intake transition invariants for Task Flow."""

from __future__ import annotations

from collections.abc import Iterable

from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.employees import EmployeeService, EmployeeServiceError
from afkbot.services.task_flow.errors import TaskFlowServiceError
from afkbot.services.task_flow.work_modes import MANAGER_INTAKE_WORK_MODE, resolve_task_work_mode
from afkbot.services.task_flow_principals import (
    EMPLOYEE_OWNER_TYPE,
    normalize_task_owner_type,
    parse_employee_owner_ref,
)
from afkbot.settings import Settings, get_settings

_COMPLETION_STATUSES = {"completed", "review"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_DELEGATION_SOURCE_TYPE = "task_delegation"


async def ensure_manager_intake_transition_allowed(
    *,
    repo: TaskFlowRepository,
    settings: Settings | None,
    profile_id: str,
    task_id: str,
    source_type: str,
    owner_type: str | None,
    owner_ref: str | None,
    labels: Iterable[str],
    target_status: str | None,
) -> None:
    """Reject finishing manager-intake work before delegated child work is complete."""

    normalized_status = str(target_status or "").strip().lower()
    if normalized_status not in _COMPLETION_STATUSES:
        return

    executor_is_manager = await _owner_is_manager(
        settings=settings,
        profile_id=profile_id,
        owner_type=owner_type,
        owner_ref=owner_ref,
    )
    work_mode = resolve_task_work_mode(
        source_type=source_type,
        labels=labels,
        executor_is_manager=executor_is_manager,
    )
    if work_mode != MANAGER_INTAKE_WORK_MODE:
        return

    delegated_rows = await repo.list_tasks_by_source(
        profile_id=profile_id,
        source_type=_DELEGATION_SOURCE_TYPE,
        source_ref=task_id,
    )
    if not delegated_rows:
        raise TaskFlowServiceError(
            error_code="manager_intake_delegation_required",
            reason=(
                "Manager intake tasks must delegate focused work before moving to "
                "review or completed. Use task.delegate, or block with a precise "
                "reason if delegation is impossible."
            ),
        )

    open_rows = [
        row
        for row in delegated_rows
        if str(row.status or "").strip().lower() not in _TERMINAL_STATUSES
    ]
    if open_rows:
        open_ids = ", ".join(row.id for row in open_rows[:5])
        raise TaskFlowServiceError(
            error_code="manager_intake_delegation_open",
            reason=(
                "Manager intake tasks cannot move to review or completed while delegated "
                f"work is still open: {open_ids}."
            ),
        )

    completed_rows = [
        row for row in delegated_rows if str(row.status or "").strip().lower() == "completed"
    ]
    if not completed_rows:
        raise TaskFlowServiceError(
            error_code="manager_intake_delegation_unsuccessful",
            reason=(
                "Manager intake tasks need at least one completed delegated child task "
                "before moving to review or completed. Replace failed or cancelled child "
                "work, or block this intake task with a precise reason."
            ),
        )


async def _owner_is_manager(
    *,
    settings: Settings | None,
    profile_id: str,
    owner_type: str | None,
    owner_ref: str | None,
) -> bool:
    normalized_owner_type = normalize_task_owner_type(owner_type)
    normalized_owner_ref = str(owner_ref or "").strip()
    if normalized_owner_type != EMPLOYEE_OWNER_TYPE or not normalized_owner_ref:
        return False
    employee_id = parse_employee_owner_ref(normalized_owner_ref)
    if employee_id is None:
        return False
    try:
        chart = await EmployeeService(settings or get_settings()).build_org_chart(
            profile_id=profile_id,
        )
    except EmployeeServiceError as exc:
        raise TaskFlowServiceError(error_code=exc.error_code, reason=exc.reason) from exc
    employee = chart.employees.get(employee_id)
    if employee is None or employee.status != "active":
        return False
    return (
        employee.manager_id is None
        or bool(employee.reports)
        or bool(employee.derived_reports)
        or bool(employee.can_delegate_to)
    )


__all__ = ["ensure_manager_intake_transition_allowed"]
