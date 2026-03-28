"""Async SQLAlchemy engine factory."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from afkbot.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Create an async database engine from settings."""

    _ensure_sqlite_directory(settings.db_url)
    engine = create_async_engine(settings.db_url, future=True)
    if _is_sqlite_url(settings.db_url):
        _configure_sqlite(engine)
    return engine


def _configure_sqlite(engine: AsyncEngine) -> None:
    """Apply SQLite connection pragmas required for integrity checks."""

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
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
