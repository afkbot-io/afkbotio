"""SQLite transient lock detection and retry helpers."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

_T = TypeVar("_T")
_SQLITE_LOCK_MESSAGES = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
)


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """Return whether one exception represents a transient SQLite lock conflict."""

    if isinstance(exc, OperationalError):
        candidates = [str(exc)]
        original = exc.orig
        if isinstance(original, BaseException):
            candidates.append(str(original))
            if isinstance(original, sqlite3.OperationalError):
                candidates.append(repr(original))
        return any(
            marker in candidate.lower()
            for candidate in candidates
            for marker in _SQLITE_LOCK_MESSAGES
        )
    cause = exc.__cause__
    if isinstance(cause, BaseException) and cause is not exc:
        return is_sqlite_lock_error(cause)
    context = exc.__context__
    if isinstance(context, BaseException) and context is not exc:
        return is_sqlite_lock_error(context)
    return False


async def run_with_sqlite_lock_retry(
    operation: Callable[[], Awaitable[_T]],
    *,
    attempts: int = 3,
    base_delay_sec: float = 0.05,
    max_delay_sec: float = 0.5,
) -> _T:
    """Retry one async operation when SQLite reports a transient lock."""

    normalized_attempts = max(1, int(attempts))
    for attempt in range(normalized_attempts):
        try:
            return await operation()
        except OperationalError as exc:
            if not is_sqlite_lock_error(exc) or attempt + 1 >= normalized_attempts:
                raise
            await asyncio.sleep(min(max_delay_sec, base_delay_sec * (2**attempt)))
    raise RuntimeError("SQLite retry loop exhausted without returning or raising")
