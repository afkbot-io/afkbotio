"""Repository for durable per-session turn queue markers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import Select, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.chat_session_turn_queue import ChatSessionTurnQueueItem


class ChatSessionTurnQueueRepository:
    """Persistence operations for turn queue admission and leases."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(
        self,
        *,
        profile_id: str,
        session_id: str,
        owner_token: str,
        source: str,
        client_msg_id: str | None,
    ) -> ChatSessionTurnQueueItem:
        """Append one queue marker and return the persisted row."""

        row = ChatSessionTurnQueueItem(
            profile_id=profile_id,
            session_id=session_id,
            owner_token=owner_token,
            client_msg_id=client_msg_id,
            source=source,
            status="queued",
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            raise
        await self._session.refresh(row)
        return row

    async def try_mark_running(
        self,
        *,
        queue_item_id: int,
        profile_id: str,
        session_id: str,
        owner_token: str,
        touched_at: datetime,
    ) -> bool:
        """Mark this row running when it is the next queued item and none is running."""

        running_statement: Select[tuple[int]] = (
            select(ChatSessionTurnQueueItem.id)
            .where(
                ChatSessionTurnQueueItem.profile_id == profile_id,
                ChatSessionTurnQueueItem.session_id == session_id,
                ChatSessionTurnQueueItem.status == "running",
            )
            .limit(1)
        )
        if (await self._session.execute(running_statement)).scalar_one_or_none() is not None:
            return False

        next_statement: Select[tuple[int]] = (
            select(ChatSessionTurnQueueItem.id)
            .where(
                ChatSessionTurnQueueItem.profile_id == profile_id,
                ChatSessionTurnQueueItem.session_id == session_id,
                ChatSessionTurnQueueItem.status == "queued",
            )
            .order_by(ChatSessionTurnQueueItem.id.asc())
            .limit(1)
        )
        next_id = (await self._session.execute(next_statement)).scalar_one_or_none()
        if next_id != queue_item_id:
            return False

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(ChatSessionTurnQueueItem)
                .where(
                    ChatSessionTurnQueueItem.id == queue_item_id,
                    ChatSessionTurnQueueItem.owner_token == owner_token,
                    ChatSessionTurnQueueItem.status == "queued",
                )
                .values(status="running", updated_at=touched_at)
            ),
        )
        return int(result.rowcount or 0) > 0

    async def touch(
        self,
        *,
        queue_item_id: int,
        owner_token: str,
        touched_at: datetime,
    ) -> bool:
        """Refresh one queue marker lease while still owned by the caller."""

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(ChatSessionTurnQueueItem)
                .where(
                    ChatSessionTurnQueueItem.id == queue_item_id,
                    ChatSessionTurnQueueItem.owner_token == owner_token,
                )
                .values(updated_at=touched_at)
            ),
        )
        return int(result.rowcount or 0) > 0

    async def release(
        self,
        *,
        queue_item_id: int,
        owner_token: str,
    ) -> None:
        """Remove this caller's queue marker."""

        await self._session.execute(
            delete(ChatSessionTurnQueueItem).where(
                ChatSessionTurnQueueItem.id == queue_item_id,
                ChatSessionTurnQueueItem.owner_token == owner_token,
            )
        )

    async def release_stale(
        self,
        *,
        older_than: datetime,
        profile_id: str,
        session_id: str,
    ) -> int:
        """Release stale queued/running markers for one session."""

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(ChatSessionTurnQueueItem).where(
                    ChatSessionTurnQueueItem.profile_id == profile_id,
                    ChatSessionTurnQueueItem.session_id == session_id,
                    ChatSessionTurnQueueItem.updated_at <= older_than,
                )
            ),
        )
        return int(result.rowcount or 0)
