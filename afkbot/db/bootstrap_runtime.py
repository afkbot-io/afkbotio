"""Runtime entrypoints for schema creation and database health checks."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from afkbot.models import load_all_models
from afkbot.models.base import Base


async def create_schema(engine: AsyncEngine) -> None:
    """Create all mapped tables for the tracked SQLite runtime."""

    load_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def list_applied_migrations(engine: AsyncEngine) -> tuple[int, ...]:
    """Return applied schema migrations for the clean SQLite baseline."""

    del engine
    return ()


async def ping(engine: AsyncEngine) -> bool:
    """Return True when the database is reachable."""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        return int(result.scalar_one()) == 1
