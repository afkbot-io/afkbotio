"""Employee organization graph validation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from afkbot.services.employees.contracts import (
    EmployeeMetadata,
    EmployeeOrgChart,
    EmployeeValidationIssue,
    EmployeeValidationReport,
)


def validate_employees(
    *,
    profile_id: str,
    employees: Iterable[EmployeeMetadata],
) -> EmployeeValidationReport:
    """Validate employee references and hierarchy constraints."""

    employee_map = {employee.id: employee for employee in employees}
    issues: list[EmployeeValidationIssue] = []
    for employee in employee_map.values():
        if employee.manager_id is not None and employee.manager_id not in employee_map:
            issues.append(
                EmployeeValidationIssue(
                    code="employee_missing_manager",
                    employee_id=employee.id,
                    target_employee_id=employee.manager_id,
                    message=f"Employee {employee.id} references missing manager {employee.manager_id}.",
                )
            )
        for report_id in employee.reports:
            if report_id not in employee_map:
                issues.append(
                    EmployeeValidationIssue(
                        code="employee_missing_report",
                        employee_id=employee.id,
                        target_employee_id=report_id,
                        message=f"Employee {employee.id} references missing report {report_id}.",
                    )
                )
                continue
            report_manager = employee_map[report_id].manager_id
            if report_manager is not None and report_manager != employee.id:
                issues.append(
                    EmployeeValidationIssue(
                        code="employee_report_conflict",
                        employee_id=employee.id,
                        target_employee_id=report_id,
                        message=(
                            f"Employee {employee.id} lists {report_id} as report, "
                            f"but {report_id} reports to {report_manager}."
                        ),
                    )
                )
        for delegate_id in employee.can_delegate_to:
            if delegate_id not in employee_map:
                issues.append(
                    EmployeeValidationIssue(
                        code="employee_missing_delegate",
                        employee_id=employee.id,
                        target_employee_id=delegate_id,
                        message=f"Employee {employee.id} references missing delegate {delegate_id}.",
                    )
                )
    issues.extend(_cycle_issues(employee_map))
    return EmployeeValidationReport(
        profile_id=profile_id,
        valid=not any(issue.severity == "error" for issue in issues),
        issues=tuple(issues),
    )


def build_org_chart(
    *,
    profile_id: str,
    employees: Iterable[EmployeeMetadata],
) -> EmployeeOrgChart:
    """Build one org chart from employee manager relationships."""

    employee_map = {employee.id: employee for employee in employees}
    reports_by_manager: dict[str, list[str]] = defaultdict(list)
    for employee in employee_map.values():
        if employee.manager_id in employee_map:
            reports_by_manager[employee.manager_id].append(employee.id)
    resolved_employees = {
        employee_id: employee.model_copy(
            update={
                "derived_reports": tuple(
                    sorted(_collect_descendant_reports(employee_id, reports_by_manager))
                )
            }
        )
        for employee_id, employee in employee_map.items()
    }
    edges = tuple(
        (manager_id, report_id)
        for manager_id in sorted(reports_by_manager)
        for report_id in sorted(reports_by_manager[manager_id])
    )
    roots = tuple(
        sorted(
            employee.id
            for employee in employee_map.values()
            if employee.manager_id is None or employee.manager_id not in employee_map
        )
    )
    return EmployeeOrgChart(
        profile_id=profile_id,
        employees=resolved_employees,
        root_employee_ids=roots,
        edges=edges,
        validation=validate_employees(profile_id=profile_id, employees=resolved_employees.values()),
    )


def _collect_descendant_reports(
    employee_id: str,
    reports_by_manager: dict[str, list[str]],
) -> set[str]:
    """Return every direct and indirect report for one employee."""

    descendants: set[str] = set()
    pending = list(reports_by_manager.get(employee_id, ()))
    while pending:
        report_id = pending.pop()
        if report_id in descendants:
            continue
        descendants.add(report_id)
        pending.extend(reports_by_manager.get(report_id, ()))
    return descendants


def _cycle_issues(employee_map: dict[str, EmployeeMetadata]) -> list[EmployeeValidationIssue]:
    issues: list[EmployeeValidationIssue] = []
    reported_cycles: set[tuple[str, ...]] = set()
    for employee_id in sorted(employee_map):
        seen_order: list[str] = []
        seen_index: dict[str, int] = {}
        current_id: str | None = employee_id
        while current_id is not None and current_id in employee_map:
            if current_id in seen_index:
                cycle = tuple(seen_order[seen_index[current_id] :])
                normalized_cycle = tuple(sorted(cycle))
                if normalized_cycle not in reported_cycles:
                    reported_cycles.add(normalized_cycle)
                    issues.append(
                        EmployeeValidationIssue(
                            code="employee_hierarchy_cycle",
                            employee_id=current_id,
                            message=f"Employee hierarchy contains a cycle: {' -> '.join(cycle)}.",
                        )
                    )
                break
            seen_index[current_id] = len(seen_order)
            seen_order.append(current_id)
            current_id = employee_map[current_id].manager_id
    return issues
