"""Async SQLAlchemy engine factory."""

from __future__ import annotations

from contextlib import suppress
from datetime import date, datetime, time
from pathlib import Path
import sqlite3
from typing import Final

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from afkbot.settings import Settings

_SQLITE_BUSY_TIMEOUT_MS: Final[int] = 5_000
_SQLITE_CODECS_REGISTERED = False


def create_engine(settings: Settings) -> AsyncEngine:
    """Create an async database engine from settings."""

    _ensure_sqlite_directory(settings.db_url)
    if _is_sqlite_url(settings.db_url):
        _register_sqlite_datetime_codecs()
    engine = create_async_engine(settings.db_url, future=True)
    setattr(engine.sync_engine, "_afkbot_settings", settings)
    if _is_sqlite_url(settings.db_url):
        _configure_sqlite(engine, db_url=settings.db_url)
    return engine


def _configure_sqlite(engine: AsyncEngine, *, db_url: str) -> None:
    """Apply SQLite connection pragmas required for integrity and lower lock contention."""

    enable_wal = _sqlite_supports_wal(db_url)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
            if enable_wal:
                journal_mode = _try_enable_sqlite_wal(cursor)
                if journal_mode == "wal":
                    cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def _ensure_sqlite_directory(db_url: str) -> None:
    """Create parent directory for on-disk SQLite URLs before engine use."""

    try:
        url = make_url(db_url)
    except Exception:
        return
    if not str(url.drivername).startswith("sqlite"):
        return
    database = str(url.database or "").strip()
    if not database or database == ":memory:":
        return
    path = Path(database).expanduser()
    target = path if path.is_absolute() else (Path.cwd() / path)
    target.parent.mkdir(parents=True, exist_ok=True)


def _is_sqlite_url(db_url: str) -> bool:
    """Return True for sqlite URLs, including sqlite+aiosqlite."""

    try:
        return str(make_url(db_url).drivername).startswith("sqlite")
    except Exception:
        return False


def _register_sqlite_datetime_codecs() -> None:
    """Install explicit SQLite date/time codecs to avoid Python 3.12 default-adapter deprecations."""

    global _SQLITE_CODECS_REGISTERED
    if _SQLITE_CODECS_REGISTERED:
        return
    sqlite3.register_adapter(date, lambda value: value.isoformat())
    sqlite3.register_adapter(time, lambda value: value.isoformat())
    sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" "))
    sqlite3.register_converter("date", lambda value: date.fromisoformat(value.decode("utf-8")))
    sqlite3.register_converter("time", lambda value: time.fromisoformat(value.decode("utf-8")))
    sqlite3.register_converter("datetime", _sqlite_datetime_from_bytes)
    sqlite3.register_converter("timestamp", _sqlite_datetime_from_bytes)
    _SQLITE_CODECS_REGISTERED = True


def _sqlite_datetime_from_bytes(value: bytes) -> datetime:
    """Decode SQLite datetime payloads using the same ISO format emitted by the adapter."""

    return datetime.fromisoformat(value.decode("utf-8"))


def _sqlite_supports_wal(db_url: str) -> bool:
    """Return True when the SQLite target is on-disk and can use WAL mode."""

    try:
        url = make_url(db_url)
    except Exception:
        return False
    database = str(url.database or "").strip()
    if not database or database == ":memory:":
        return False
    if database.startswith("file:") and str(url.query.get("mode") or "").strip().lower() == "memory":
        return False
    return True


def _try_enable_sqlite_wal(cursor) -> str | None:  # type: ignore[no-untyped-def]
    """Best-effort WAL activation that never aborts the SQLite connect path."""

    with suppress(Exception):
        cursor.execute("PRAGMA journal_mode=WAL")
        row = cursor.fetchone()
        if not row:
            return None
        return str(row[0]).strip().lower() or None
    return None
