"""Async database session helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import logging
from threading import Lock
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql.ddl import DDLElement
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause

from afkbot.db.sqlite_lock import (
    SqliteFileLock,
    SqliteScopeLockTarget,
    acquire_sqlite_file_lock,
    mark_sqlite_write_key_held,
    sqlite_lock_target_for_bind,
    sqlite_write_key_is_held,
    unmark_sqlite_write_key_held,
)

_SQLITE_WRITE_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
_SQLITE_WRITE_LOCKS_GUARD = Lock()
_LOGGER = logging.getLogger(__name__)
SESSION_JSONL_PENDING_OUTBOX_INFO_KEY = "afkbot_session_jsonl_pending_outbox_ids"


@dataclass(slots=True)
class _SqliteWriteLease:
    """Held SQLite write gate for one transaction or explicit write section."""

    lock: asyncio.Lock
    file_lock: SqliteFileLock | None
    lock_key: str

    async def release(self) -> None:
        """Release the cross-process lock first, then the event-loop waiter."""

        try:
            if self.file_lock is not None:
                await asyncio.to_thread(self.file_lock.release)
        finally:
            try:
                unmark_sqlite_write_key_held(self.lock_key)
            finally:
                self.lock.release()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create async session factory bound to the given engine."""

    return async_sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        class_=SerializedSQLiteSession,
    )


def create_peer_session_factory(
    session: AsyncSession,
) -> async_sessionmaker[AsyncSession] | None:
    """Create an independent session factory for committed peer reads."""

    bind = session.bind
    if bind is None:
        return None
    return async_sessionmaker(
        bind=bind,
        autoflush=False,
        expire_on_commit=False,
        class_=SerializedSQLiteSession,
    )


class SerializedSQLiteSession(AsyncSession):
    """AsyncSession that serializes SQLite write operations without blocking long reads."""

    _sqlite_write_lease: _SqliteWriteLease | None
    _after_commit_callbacks: list[Callable[[], None]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._sqlite_write_lease = None
        self._after_commit_callbacks = []

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute statements, gating SQLite writes across tasks and processes."""

        if _is_write_statement(statement):
            await self._ensure_sqlite_write_lease()
        return await super().execute(statement, *args, **kwargs)

    async def flush(self, *args: Any, **kwargs: Any) -> None:
        """Flush pending ORM writes while holding the SQLite write gate."""

        if self.new or self.dirty or self.deleted:
            await self._ensure_sqlite_write_lease()
        await super().flush(*args, **kwargs)

    async def commit(self) -> None:
        """Commit pending work and release the SQLite write gate."""

        if self.new or self.dirty or self.deleted:
            await self._ensure_sqlite_write_lease()
        try:
            await super().commit()
        except BaseException:
            self._after_commit_callbacks.clear()
            raise
        else:
            await self._run_after_commit_callbacks()
        finally:
            self.info.pop(SESSION_JSONL_PENDING_OUTBOX_INFO_KEY, None)
            await self._release_sqlite_write_lease()

    async def rollback(self) -> None:
        """Rollback pending work and release the SQLite write gate."""

        try:
            await super().rollback()
        finally:
            self._after_commit_callbacks.clear()
            self.info.pop(SESSION_JSONL_PENDING_OUTBOX_INFO_KEY, None)
            await self._release_sqlite_write_lease()

    async def close(self) -> None:
        """Close the session and release any held SQLite write gate."""

        try:
            await super().close()
        finally:
            self._after_commit_callbacks.clear()
            self.info.pop(SESSION_JSONL_PENDING_OUTBOX_INFO_KEY, None)
            await self._release_sqlite_write_lease()

    def add_after_commit_callback(self, callback: Callable[[], None]) -> None:
        """Run one synchronous callback after the next successful commit."""

        self._after_commit_callbacks.append(callback)

    async def _run_after_commit_callbacks(self) -> None:
        """Execute and clear callbacks registered by file-backed stores."""

        callbacks = self._after_commit_callbacks
        if not callbacks:
            return
        self._after_commit_callbacks = []
        for callback in callbacks:
            try:
                await asyncio.to_thread(callback)
            except Exception:
                _LOGGER.exception("after_commit_callback_failed")

    async def _ensure_sqlite_write_lease(self) -> None:
        """Hold the SQLite write gate until this session finishes its transaction."""

        if self._sqlite_write_lease is not None:
            return
        lock_target = sqlite_lock_target_for_bind(self.bind)
        if lock_target is None:
            return
        if sqlite_write_key_is_held(lock_target.key):
            return
        self._sqlite_write_lease = await _acquire_sqlite_write_lease(lock_target)

    async def _release_sqlite_write_lease(self) -> None:
        """Release this session's held SQLite write gate if it has one."""

        lease = self._sqlite_write_lease
        if lease is None:
            return
        self._sqlite_write_lease = None
        await lease.release()


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session and commit/rollback automatically."""

    session = session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def session_write_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session while holding the SQLite write gate for the full unit of work."""

    session = session_factory()
    try:
        async with sqlite_write_gate_for_bind(session.bind):
            yield session
            await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


@asynccontextmanager
async def sqlite_write_gate_for_bind(bind: object) -> AsyncIterator[None]:
    """Serialize one SQLite write section for an engine/connection bind."""

    lock_target = sqlite_lock_target_for_bind(bind)
    if lock_target is None:
        yield
        return

    if sqlite_write_key_is_held(lock_target.key):
        yield
        return

    lease = await _acquire_sqlite_write_lease(lock_target)
    try:
        yield
    finally:
        await lease.release()


@asynccontextmanager
async def sqlite_write_gate_for_engine(
    engine: AsyncEngine,
) -> AsyncIterator[None]:
    """Serialize one SQLite write section for an async engine."""

    async with sqlite_write_gate_for_bind(engine):
        yield


def add_after_commit_callback(session: AsyncSession, callback: Callable[[], None]) -> bool:
    """Register callback on supported sessions, returning whether it was deferred."""

    if not session.in_transaction():
        return False
    if not isinstance(session, SerializedSQLiteSession):
        return False
    session.add_after_commit_callback(callback)
    return True


def _is_write_statement(statement: object) -> bool:
    """Return whether a SQLAlchemy statement can mutate SQLite state."""

    if isinstance(statement, Insert | Update | Delete | DDLElement):
        return True
    if isinstance(statement, TextClause):
        normalized = " ".join(statement.text.strip().lower().split())
        write_prefixes = (
            "insert ",
            "update ",
            "delete ",
            "create ",
            "alter ",
            "drop ",
            "replace ",
            "pragma ",
            "vacuum",
            "reindex",
            "analyze",
            "attach ",
            "detach ",
            "begin immediate",
            "begin exclusive",
        )
        if normalized.startswith(write_prefixes):
            return True
        if normalized.startswith("with "):
            return any(
                token in normalized
                for token in (" insert ", " update ", " delete ", " replace ")
            )
        return False
    return False


def _sqlite_write_lock(lock_key: str) -> asyncio.Lock:
    """Return the event-loop-local asyncio lock for one SQLite database key."""

    loop_key = (id(asyncio.get_running_loop()), lock_key)
    with _SQLITE_WRITE_LOCKS_GUARD:
        lock = _SQLITE_WRITE_LOCKS.get(loop_key)
        if lock is None:
            lock = asyncio.Lock()
            _SQLITE_WRITE_LOCKS[loop_key] = lock
        return lock


async def _acquire_sqlite_write_lease(lock_target: SqliteScopeLockTarget) -> _SqliteWriteLease:
    """Acquire the event-loop and cross-process write gate for one SQLite database."""

    lock = _sqlite_write_lock(lock_target.key)
    await lock.acquire()
    try:
        file_lock = await acquire_sqlite_file_lock(lock_target.file_lock_path)
    except BaseException:
        lock.release()
        raise
    mark_sqlite_write_key_held(lock_target.key)
    return _SqliteWriteLease(
        lock=lock,
        file_lock=file_lock,
        lock_key=lock_target.key,
    )
