"""Shared timing input normalization for Task Flow tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Collection


@dataclass(frozen=True)
class ReadyAtUpdate:
    """Normalized ready_at update requested by one tool payload."""

    should_update: bool
    ready_at: datetime | None = None


class TaskTimingInputError(ValueError):
    """Raised when mutually exclusive timing inputs are provided."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def resolve_ready_at_update(
    *,
    explicit_fields: Collection[str],
    ready_at: datetime | None,
    retry_after_sec: int | None,
    status: str | None,
    require_blocked_status_for_retry: bool,
    now_utc: datetime | None = None,
) -> ReadyAtUpdate:
    """Normalize ready_at/retry_after_sec without treating explicit nulls as conflicts."""

    ready_at_explicit = "ready_at" in explicit_fields
    retry_after_explicit = "retry_after_sec" in explicit_fields
    retry_after_requested = retry_after_explicit and retry_after_sec is not None
    ready_at_requested = ready_at_explicit and ready_at is not None
    if ready_at_requested and retry_after_requested:
        raise TaskTimingInputError(
            error_code="task_ready_at_conflict",
            reason="ready_at and retry_after_sec cannot be used together",
        )
    if retry_after_requested:
        assert retry_after_sec is not None
        if require_blocked_status_for_retry and status != "blocked":
            raise TaskTimingInputError(
                error_code="task_retry_after_requires_blocked_status",
                reason="retry_after_sec requires status=blocked",
            )
        reference = now_utc or datetime.now(timezone.utc)
        return ReadyAtUpdate(
            should_update=True,
            ready_at=reference + timedelta(seconds=retry_after_sec),
        )
    if ready_at_explicit:
        return ReadyAtUpdate(should_update=True, ready_at=ready_at)
    return ReadyAtUpdate(should_update=False)


__all__ = [
    "ReadyAtUpdate",
    "TaskTimingInputError",
    "resolve_ready_at_update",
]
