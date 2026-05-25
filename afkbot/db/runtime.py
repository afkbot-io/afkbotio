"""Shared database runtime lifecycle helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.settings import Settings, get_settings


@dataclass(frozen=True, slots=True)
class DatabaseSessionResources:
    """Resolved session factory plus optional owned engine for one operation."""

    session_factory: async_sessionmaker[AsyncSession]
    owned_engine: AsyncEngine | None = None

    @property
    def owns_engine(self) -> bool:
        """Return whether this resource wrapper owns the engine lifecycle."""

        return self.owned_engine is not None


@dataclass(slots=True)
class DatabaseRuntime:
    """Long-lived DB engine/session/schema lifecycle for runtime services."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    _schema_ready: bool = False
    _schema_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    @classmethod
    def create(cls, settings: Settings | None = None) -> DatabaseRuntime:
        """Create a long-lived database runtime from settings."""

        resolved_settings = settings or get_settings()
        engine = create_engine(resolved_settings)
        return cls(
            settings=resolved_settings,
            engine=engine,
            session_factory=create_session_factory(engine),
        )

    async def ensure_schema(self) -> None:
        """Create or upgrade schema once for this runtime instance."""

        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            from afkbot.db.bootstrap import create_schema

            await create_schema(self.engine)
            self._schema_ready = True

    @asynccontextmanager
    async def session(self, *, ensure_schema: bool = True) -> AsyncIterator[AsyncSession]:
        """Yield one managed session from this runtime."""

        if ensure_schema:
            await self.ensure_schema()
        async with session_scope(self.session_factory) as db:
            yield db

    async def dispose(self) -> None:
        """Dispose the owned async engine."""

        await self.engine.dispose()


async def create_owned_session_resources(settings: Settings | None = None) -> DatabaseSessionResources:
    """Create one short-lived engine/session factory pair with initialized schema."""

    resolved_settings = settings or get_settings()
    owned_engine = create_engine(resolved_settings)
    from afkbot.db.bootstrap import create_schema

    await create_schema(owned_engine)
    return DatabaseSessionResources(
        session_factory=create_session_factory(owned_engine),
        owned_engine=owned_engine,
    )


async def resolve_session_resources(
    *,
    shared_session_factory: async_sessionmaker[AsyncSession] | None,
    settings: Settings | None = None,
) -> DatabaseSessionResources:
    """Return shared DB resources or create temporary owned resources on demand."""

    if shared_session_factory is not None:
        return DatabaseSessionResources(session_factory=shared_session_factory)
    return await create_owned_session_resources(settings=settings)


async def dispose_session_resources(resources: DatabaseSessionResources) -> None:
    """Dispose the owned engine when one was created for the current operation."""

    if resources.owned_engine is not None:
        await resources.owned_engine.dispose()
