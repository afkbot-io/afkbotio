"""Durable persistence for pending channel ingress batching."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.channel_ingress_pending_event_repo import ChannelIngressPendingEventRepository
from afkbot.services.channels.ingress_coalescer import ChannelIngressBatch, ChannelIngressEvent
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[tuple[str, int], "ChannelIngressPendingService"] = {}
TPendingValue = TypeVar("TPendingValue")


class ChannelIngressPendingService:
    """Persist pending ingress events until one batch flush succeeds."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine = create_engine(settings)
        self._session_factory: async_sessionmaker[AsyncSession] = create_session_factory(self._engine)
        self._schema_ready = False
        self._schema_lock = asyncio.Lock()

    async def record_pending(self, *, event: ChannelIngressEvent) -> bool:
        """Persist one pending ingress event; return false when already stored."""

        async def _op(session: AsyncSession) -> bool:
            repo = ChannelIngressPendingEventRepository(session)
            row = await repo.create(
                endpoint_id=event.endpoint_id,
                transport=event.transport,
                conversation_key=event.conversation_key,
                event_key=event.event_key,
                message_id=event.message_id,
                account_id=event.account_id,
                peer_id=event.peer_id,
                thread_id=event.thread_id,
                user_id=event.user_id,
                text=event.text,
                observed_at=event.observed_at,
                chat_kind=event.chat_kind,
                source_event_id=event.source_event_id,
            )
            return row is not None

        return await self._with_session(_op)

    async def list_pending(self, *, endpoint_id: str) -> list[ChannelIngressEvent]:
        """Return pending ingress events for one endpoint in stable order."""

        async def _op(session: AsyncSession) -> list[ChannelIngressEvent]:
            repo = ChannelIngressPendingEventRepository(session)
            rows = await repo.list_by_endpoint(endpoint_id=endpoint_id)
            return [
                ChannelIngressEvent(
                    endpoint_id=row.endpoint_id,
                    transport=row.transport,
                    account_id=row.account_id,
                    peer_id=row.peer_id,
                    thread_id=row.thread_id,
                    user_id=row.user_id,
                    event_key=row.event_key,
                    message_id=row.message_id,
                    text=row.text,
                    observed_at=row.observed_at,
                    chat_kind=row.chat_kind,
                    source_event_id=row.source_event_id,
                )
                for row in rows
            ]

        return await self._with_session(_op)

    async def release_batch(self, *, batch: ChannelIngressBatch) -> int:
        """Delete persisted pending rows for one successfully flushed batch."""

        async def _op(session: AsyncSession) -> int:
            repo = ChannelIngressPendingEventRepository(session)
            return await repo.delete_by_event_keys(
                endpoint_id=batch.endpoint_id,
                event_keys=tuple(item.event_key for item in batch.events),
            )

        return await self._with_session(_op)

    async def release_event(self, *, endpoint_id: str, event_key: str) -> int:
        """Delete one persisted pending event by natural key."""

        async def _op(session: AsyncSession) -> int:
            repo = ChannelIngressPendingEventRepository(session)
            return await repo.delete_by_event_keys(
                endpoint_id=endpoint_id,
                event_keys=(event_key,),
            )

        return await self._with_session(_op)

    async def delete_by_endpoint(self, *, endpoint_id: str) -> int:
        """Delete all pending ingress rows for one endpoint."""

        async def _op(session: AsyncSession) -> int:
            repo = ChannelIngressPendingEventRepository(session)
            return await repo.delete_by_endpoint(endpoint_id=endpoint_id)

        return await self._with_session(_op)

    async def _with_session(
        self,
        op: Callable[[AsyncSession], Awaitable[TPendingValue]],
    ) -> TPendingValue:
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


def get_channel_ingress_pending_service(settings: Settings) -> ChannelIngressPendingService:
    """Return loop-safe pending-ingress persistence service for one runtime root."""

    root_key = str(settings.root_dir.resolve())
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        return ChannelIngressPendingService(settings)
    key = (root_key, loop_id)
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ChannelIngressPendingService(settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_channel_ingress_pending_services() -> None:
    """Reset cached pending-ingress services for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_channel_ingress_pending_services_async() -> None:
    """Reset cached pending-ingress services and dispose their DB engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
