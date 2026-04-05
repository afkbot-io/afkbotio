"""Errors raised by the Task Flow service."""

from __future__ import annotations


class TaskFlowServiceError(ValueError):
    """Structured service error for task flow operations."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
