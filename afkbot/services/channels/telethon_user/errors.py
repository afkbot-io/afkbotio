"""Structured errors for the Telethon user-channel integration."""

from __future__ import annotations


class TelethonUserServiceError(ValueError):
    """Structured Telethon userbot failure surfaced to CLI/runtime callers."""

    def __init__(
        self,
        *,
        error_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
        self.metadata = {} if metadata is None else metadata
