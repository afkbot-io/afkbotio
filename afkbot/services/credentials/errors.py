"""Shared error types for credentials service modules."""

from __future__ import annotations


class CredentialsServiceError(Exception):
    """Application error for credentials service methods."""

    def __init__(
        self,
        *,
        error_code: str,
        reason: str,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
        self.details = {} if details is None else details
