"""Database dialect helpers shared by runtime persistence code."""

from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession


def database_driver_name(db_url: str) -> str:
    """Return the SQLAlchemy driver name for a database URL, or an empty string."""

    try:
        return str(make_url(db_url).drivername)
    except Exception:
        return ""


def is_sqlite_url(db_url: str) -> bool:
    """Return True for SQLite URLs, including sqlite+aiosqlite."""

    return database_driver_name(db_url).startswith("sqlite")


def is_postgres_url(db_url: str) -> bool:
    """Return True for PostgreSQL URLs supported by SQLAlchemy async engines."""

    return database_driver_name(db_url).startswith("postgresql")


def session_dialect_name(session: AsyncSession) -> str:
    """Return the bound SQLAlchemy dialect name for an async session."""

    bind = session.get_bind()
    return "" if bind is None else str(bind.dialect.name)
