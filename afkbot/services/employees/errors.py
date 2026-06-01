"""Errors for employee descriptor services."""

from __future__ import annotations


class EmployeeServiceError(ValueError):
    """Structured employee service error with a stable error code."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
