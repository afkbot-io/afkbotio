"""Identifier validation for Task Flow employees."""

from __future__ import annotations

import re

from afkbot.services.employees.errors import EmployeeServiceError

_EMPLOYEE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")


def validate_employee_id(employee_id: str) -> str:
    """Return a normalized employee id or raise a service error."""

    normalized = str(employee_id or "").strip()
    if not _EMPLOYEE_ID_RE.match(normalized):
        raise EmployeeServiceError(
            error_code="invalid_employee_id",
            reason=f"Invalid employee id: {employee_id}",
        )
    return normalized
