"""Contracts for profile-scoped Task Flow employees."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EmployeeStatus = Literal["active", "disabled", "archived"]
ValidationSeverity = Literal["error", "warning"]


class EmployeeMetadata(BaseModel):
    """One validated employee descriptor loaded from markdown."""

    model_config = ConfigDict(extra="forbid")

    id: str
    profile_id: str
    name: str
    title: str
    role: str
    status: EmployeeStatus
    manager_id: str | None = None
    reports: tuple[str, ...] = ()
    derived_reports: tuple[str, ...] = ()
    can_delegate_to: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    can_use_subagents: bool = False
    subagent_allowlist: tuple[str, ...] = ()
    max_active_tasks: int = Field(default=1, ge=1)
    body: str = ""


class EmployeeValidationIssue(BaseModel):
    """One actionable employee validation issue."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: ValidationSeverity = "error"
    employee_id: str | None = None
    target_employee_id: str | None = None


class EmployeeValidationReport(BaseModel):
    """Validation result for one profile employee graph."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    valid: bool
    issues: tuple[EmployeeValidationIssue, ...] = ()


class EmployeeOrgChart(BaseModel):
    """Resolved employee graph for one profile."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    employees: dict[str, EmployeeMetadata]
    root_employee_ids: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    validation: EmployeeValidationReport
