"""Repository for durable pending channel ingress events."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import Select, delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.channel_ingress_pending_event import ChannelIngressPendingEvent


class ChannelIngressPendingEventRepository:
    """Persistence operations for pending channel ingress events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        endpoint_id: str,
        transport: str,
        conversation_key: str,
        event_key: str,
        message_id: str,
        account_id: str,
        peer_id: str,
        thread_id: str | None,
        user_id: str | None,
        text: str,
        observed_at: str,
        chat_kind: str | None,
        source_event_id: str | None,
    ) -> ChannelIngressPendingEvent | None:
        """Persist one pending event; return None when it already exists."""

        row = ChannelIngressPendingEvent(
            endpoint_id=endpoint_id,
            transport=transport,
            conversation_key=conversation_key,
            event_key=event_key,
            message_id=message_id,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
            text=text,
            observed_at=observed_at,
            chat_kind=chat_kind,
            source_event_id=source_event_id,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            return None
        await self._session.refresh(row)
        return row

    async def list_by_endpoint(self, *, endpoint_id: str) -> list[ChannelIngressPendingEvent]:
        """List pending events for one endpoint in stable oldest-first order."""

        statement: Select[tuple[ChannelIngressPendingEvent]] = (
            select(ChannelIngressPendingEvent)
            .where(ChannelIngressPendingEvent.endpoint_id == endpoint_id)
            .order_by(
                ChannelIngressPendingEvent.created_at.asc(),
                ChannelIngressPendingEvent.id.asc(),
            )
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def delete_by_event_keys(
        self,
        *,
        endpoint_id: str,
        event_keys: Sequence[str],
    ) -> int:
        """Delete selected pending events by natural key and return affected count."""

        if not event_keys:
            return 0
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(ChannelIngressPendingEvent).where(
                    ChannelIngressPendingEvent.endpoint_id == endpoint_id,
                    ChannelIngressPendingEvent.event_key.in_(tuple(event_keys)),
                )
            ),
        )
        return int(result.rowcount or 0)

    async def delete_by_endpoint(self, *, endpoint_id: str) -> int:
        """Delete all pending events for one endpoint and return affected count."""

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(ChannelIngressPendingEvent).where(
                    ChannelIngressPendingEvent.endpoint_id == endpoint_id
                )
            ),
        )
        return int(result.rowcount or 0)
