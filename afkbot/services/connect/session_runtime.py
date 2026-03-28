"""Session helpers for connect service DB-backed operations."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.settings import Settings
from afkbot.settings import get_settings


@asynccontextmanager
async def connect_session_scope(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    settings: Settings | None = None,
    engine_factory: Callable[[Settings], AsyncEngine] = create_engine,
) -> AsyncIterator[AsyncSession]:
    """Yield a session for connect operations, owning engine lifecycle when needed."""

    engine = None
    resolved_factory = session_factory
    if resolved_factory is None:
        resolved_settings = settings or get_settings()
        engine = engine_factory(resolved_settings)
        resolved_factory = create_session_factory(engine)
        await create_schema(engine)

    try:
        async with session_scope(resolved_factory) as db:
            yield db
    finally:
        if engine is not None:
            await engine.dispose()
