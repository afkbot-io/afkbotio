"""Shared SQLite write-lock primitives."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy.engine import Engine, make_url
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True, slots=True)
class SqliteScopeLockTarget:
    """Event-loop and cross-process lock coordinates for one SQLite database."""

    key: str
    file_lock_path: Path | None


_HELD_SQLITE_WRITE_KEYS: ContextVar[tuple[tuple[str, int], ...]] = ContextVar(
    "afkbot_held_sqlite_write_keys",
    default=(),
)


@dataclass(slots=True)
class SqliteFileLock:
    """Held cross-process SQLite lock file."""

    file_obj: BinaryIO

    def release(self) -> None:
        """Release and close the held lock file."""

        try:
            import fcntl

            fcntl.flock(self.file_obj, fcntl.LOCK_UN)
        finally:
            self.file_obj.close()


async def acquire_sqlite_file_lock(lock_path: Path | None) -> SqliteFileLock | None:
    """Acquire a cross-process SQLite lock without blocking the event loop."""

    if lock_path is None:
        return None
    return await asyncio.to_thread(acquire_sqlite_file_lock_sync, lock_path)


def acquire_sqlite_file_lock_sync(
    lock_path: Path,
    *,
    blocking: bool = True,
) -> SqliteFileLock | None:
    """Synchronously acquire one advisory SQLite lock file."""

    try:
        import fcntl
    except ImportError:
        return None
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    file_obj = lock_path.open("a+b")
    try:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(file_obj, flags)
    except BlockingIOError:
        file_obj.close()
        return None
    except BaseException:
        file_obj.close()
        raise
    return SqliteFileLock(file_obj=file_obj)


@contextmanager
def sqlite_file_lock_sync(
    lock_path: Path | None,
    *,
    blocking: bool = True,
) -> Iterator[bool]:
    """Synchronously hold a SQLite advisory lock around connection-time writes."""

    if lock_path is None:
        yield True
        return
    held_lock = acquire_sqlite_file_lock_sync(lock_path, blocking=blocking)
    if held_lock is None and not blocking:
        yield False
        return
    try:
        yield True
    finally:
        if held_lock is not None:
            held_lock.release()


def sqlite_write_key_is_held(lock_key: str) -> bool:
    """Return whether this execution context already owns a SQLite write gate."""

    return (lock_key, _current_task_marker()) in _HELD_SQLITE_WRITE_KEYS.get()


def mark_sqlite_write_key_held(lock_key: str) -> None:
    """Mark one SQLite write gate as held in the current execution context."""

    held_keys = _HELD_SQLITE_WRITE_KEYS.get()
    marker = (lock_key, _current_task_marker())
    if marker in held_keys:
        return
    _HELD_SQLITE_WRITE_KEYS.set((*held_keys, marker))


def unmark_sqlite_write_key_held(lock_key: str) -> None:
    """Clear one SQLite write-gate marker from the current execution context."""

    held_keys = _HELD_SQLITE_WRITE_KEYS.get()
    marker = (lock_key, _current_task_marker())
    if marker not in held_keys:
        return
    _HELD_SQLITE_WRITE_KEYS.set(tuple(key for key in held_keys if key != marker))


def _current_task_marker() -> int:
    """Return a stable reentrancy owner id for the current asyncio task."""

    try:
        task = asyncio.current_task()
    except RuntimeError:
        return 0
    return 0 if task is None else id(task)


def sqlite_lock_target_for_bind(bind: object) -> SqliteScopeLockTarget | None:
    """Return stable lock coordinates for SQLite engine/connection binds."""

    url = _bind_url(bind)
    if url is None:
        return None
    return sqlite_lock_target_for_url(url)


def sqlite_lock_target_for_url(db_url: object) -> SqliteScopeLockTarget | None:
    """Return stable lock coordinates for a SQLite URL-like value."""

    try:
        url = make_url(str(db_url))
    except Exception:
        return None
    if not str(url.drivername).startswith("sqlite"):
        return None
    database = str(url.database or "").strip()
    if not database or database == ":memory:":
        return SqliteScopeLockTarget(key=f"sqlite:{url}", file_lock_path=None)
    if database.startswith("file:"):
        return SqliteScopeLockTarget(key=f"sqlite:{database}", file_lock_path=None)
    path = Path(database).expanduser()
    resolved = path if path.is_absolute() else Path.cwd() / path
    db_path = resolved.resolve(strict=False)
    return SqliteScopeLockTarget(
        key=f"sqlite:{db_path}",
        file_lock_path=db_path.with_suffix(f"{db_path.suffix}.afkbot.lock"),
    )


def _bind_url(bind: object) -> Any | None:
    """Best-effort extraction of a SQLAlchemy URL from async/sync binds."""

    if isinstance(bind, AsyncEngine):
        return bind.sync_engine.url
    if isinstance(bind, Engine):
        return bind.url
    sync_engine = getattr(bind, "sync_engine", None)
    if sync_engine is not None and hasattr(sync_engine, "url"):
        return sync_engine.url
    engine = getattr(bind, "engine", None)
    if isinstance(engine, AsyncEngine):
        return engine.sync_engine.url
    if isinstance(engine, Engine):
        return engine.url
    raw_url = getattr(bind, "url", None)
    if raw_url is not None:
        with contextlib.suppress(Exception):
            return make_url(str(raw_url))
    return None
