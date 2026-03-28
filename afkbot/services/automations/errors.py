"""Shared errors for automation service flows."""

from __future__ import annotations


class AutomationsServiceError(Exception):
    """Application error for automations service methods."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
