"""Shared errors for automation service flows."""

from __future__ import annotations


class AutomationsServiceError(Exception):
    """Application error for automations service methods."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


NONRETRYABLE_AUTOMATION_ERROR_CODES = frozenset(
    {
        "automation_graph_failed_after_unsafe_effect",
        "automation_graph_fallback_failed_after_unsafe_effect",
    }
)


def is_nonretryable_automation_error(exc: BaseException) -> bool:
    """Return whether a failed automation claim must not replay the same event."""

    return (
        isinstance(exc, AutomationsServiceError)
        and exc.error_code in NONRETRYABLE_AUTOMATION_ERROR_CODES
    )
