"""Profile-scoped Task Flow employee descriptors."""

from afkbot.services.employees.contracts import (
    EmployeeMetadata,
    EmployeeOrgChart,
    EmployeeValidationIssue,
    EmployeeValidationReport,
)
from afkbot.services.employees.errors import EmployeeServiceError
from afkbot.services.employees.service import EmployeeService

__all__ = [
    "EmployeeMetadata",
    "EmployeeOrgChart",
    "EmployeeService",
    "EmployeeServiceError",
    "EmployeeValidationIssue",
    "EmployeeValidationReport",
]
