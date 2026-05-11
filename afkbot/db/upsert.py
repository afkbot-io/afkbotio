"""Dialect-aware INSERT helpers for SQLAlchemy upsert call sites."""

from __future__ import annotations

from typing import Any

from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.dialect import session_dialect_name


def upsert_insert_for_session(session: AsyncSession, model: type[Any]) -> Any:
    """Return an INSERT builder with `on_conflict_do_update` for the active dialect."""

    return upsert_insert_for_dialect(dialect_name=session_dialect_name(session), model=model)


def upsert_insert_for_dialect(*, dialect_name: str, model: type[Any]) -> Any:
    """Return a dialect-specific INSERT builder for SQLite and PostgreSQL."""

    if dialect_name == "postgresql":
        return postgres_insert(model)
    return sqlite_insert(model)
