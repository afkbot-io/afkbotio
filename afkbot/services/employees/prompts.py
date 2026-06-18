"""Prompt overlays for Task Flow employee execution."""

from __future__ import annotations

from afkbot.services.employees.contracts import EmployeeMetadata


def build_employee_prompt_overlay(employee: EmployeeMetadata) -> str:
    """Build a deterministic prompt overlay for one Task Flow employee."""

    lines = [
        "Employee execution context.",
        f"- employee_id: {employee.id}",
        f"- name: {employee.name}",
        f"- title: {employee.title}",
        f"- role: {employee.role}",
        f"- status: {employee.status}",
    ]
    if employee.manager_id is not None:
        lines.append(f"- manager_id: {employee.manager_id}")
    if employee.derived_reports:
        lines.append(f"- reports: {', '.join(employee.derived_reports)}")
    elif employee.reports:
        lines.append(f"- reports: {', '.join(employee.reports)}")
    if employee.can_delegate_to:
        lines.append(f"- can_delegate_to: {', '.join(employee.can_delegate_to)}")
    if employee.allowed_tools:
        lines.append(f"- allowed_tools: {', '.join(employee.allowed_tools)}")
        lines.append("- allowed_tools are a hard runtime gate; delegate work instead of calling tools outside this list.")
    lines.append(f"- can_use_subagents: {str(employee.can_use_subagents).lower()}")
    if employee.subagent_allowlist:
        lines.append(f"- subagent_allowlist: {', '.join(employee.subagent_allowlist)}")
    if employee.body:
        lines.extend(("", "Employee descriptor:", employee.body))
    return "\n".join(lines)
