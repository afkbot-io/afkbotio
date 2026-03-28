"""Shared inbound-event journal used by channel adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.channel_ingress_event_repo import ChannelIngressEventRepository
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "ChannelIngressJournalService"] = {}
TJournalValue = TypeVar("TJournalValue")


class ChannelIngressJournalService:
    """Persist processed adapter event keys to suppress duplicate ingress handling."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_engine(settings)
        self._session_factory: async_sessionmaker[AsyncSession] = create_session_factory(self._engine)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def contains(self, *, endpoint_id: str, event_key: str) -> bool:
        """Return true when the adapter event key has already been processed."""

        async def _op(session: AsyncSession) -> bool:
            repo = ChannelIngressEventRepository(session)
            return await repo.get_by_key(endpoint_id=endpoint_id, event_key=event_key) is not None

        return await self._with_session(_op)

    async def try_claim(
        self,
        *,
        endpoint_id: str,
        transport: str,
        event_key: str,
    ) -> bool:
        """Atomically claim one adapter event key for processing."""

        async def _op(session: AsyncSession) -> bool:
            repo = ChannelIngressEventRepository(session)
            created = await repo.create(
                endpoint_id=endpoint_id,
                transport=transport,
                event_key=event_key,
            )
            return created is not None

        return await self._with_session(_op)

    async def record_processed(
        self,
        *,
        endpoint_id: str,
        transport: str,
        event_key: str,
    ) -> bool:
        """Persist one processed adapter event; return false when it already exists."""

        async def _op(session: AsyncSession) -> bool:
            repo = ChannelIngressEventRepository(session)
            created = await repo.create(
                endpoint_id=endpoint_id,
                transport=transport,
                event_key=event_key,
            )
            return created is not None

        return await self._with_session(_op)

    async def release_claim(
        self,
        *,
        endpoint_id: str,
        event_key: str,
    ) -> int:
        """Release one claimed event key so a later retry may process it again."""

        async def _op(session: AsyncSession) -> int:
            repo = ChannelIngressEventRepository(session)
            return await repo.delete_by_key(
                endpoint_id=endpoint_id,
                event_key=event_key,
            )

        return await self._with_session(_op)

    async def _with_session(
        self,
        op: Callable[[AsyncSession], Awaitable[TJournalValue]],
    ) -> TJournalValue:
        await self._ensure_schema()
        async with session_scope(self._session_factory) as session:
            return await op(session)

    async def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._schema_lock:
            if self._schema_ready:
                return
            await create_schema(self._engine)
            self._schema_ready = True

    async def shutdown(self) -> None:
        """Dispose owned database engine."""

        await self._engine.dispose()


def get_channel_ingress_journal_service(settings: Settings) -> ChannelIngressJournalService:
    """Return cached ingress journal service for one runtime root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ChannelIngressJournalService(settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_channel_ingress_journal_services() -> None:
    """Reset cached ingress journal services for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_channel_ingress_journal_services_async() -> None:
    """Reset cached ingress journal services and dispose their DB engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
