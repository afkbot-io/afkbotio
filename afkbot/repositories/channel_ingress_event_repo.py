"""Repository for channel adapter inbound event journaling."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete
from sqlalchemy import Select, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.channel_ingress_event import ChannelIngressEvent


class ChannelIngressEventRepository:
    """Persistence operations for processed adapter ingress events."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_key(
        self,
        *,
        endpoint_id: str,
        event_key: str,
    ) -> ChannelIngressEvent | None:
        """Return one processed event row by natural key."""

        statement: Select[tuple[ChannelIngressEvent]] = select(ChannelIngressEvent).where(
            ChannelIngressEvent.endpoint_id == endpoint_id,
            ChannelIngressEvent.event_key == event_key,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def create(
        self,
        *,
        endpoint_id: str,
        transport: str,
        event_key: str,
    ) -> ChannelIngressEvent | None:
        """Persist one processed event row; return None when it already exists."""

        row = ChannelIngressEvent(
            endpoint_id=endpoint_id,
            transport=transport,
            event_key=event_key,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            return None
        await self._session.refresh(row)
        return row

    async def delete_by_endpoint(self, *, endpoint_id: str) -> int:
        """Delete all journal rows for one endpoint and return affected count."""

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(ChannelIngressEvent).where(ChannelIngressEvent.endpoint_id == endpoint_id)
            ),
        )
        return int(result.rowcount or 0)

    async def delete_by_key(
        self,
        *,
        endpoint_id: str,
        event_key: str,
    ) -> int:
        """Delete one journal row by natural key and return affected count."""

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(ChannelIngressEvent).where(
                    ChannelIngressEvent.endpoint_id == endpoint_id,
                    ChannelIngressEvent.event_key == event_key,
                )
            ),
        )
        return int(result.rowcount or 0)
