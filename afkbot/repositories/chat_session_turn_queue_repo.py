"""Repository for durable per-session turn queue markers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Select, and_, case, delete, func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.chat_session_turn_queue import ChatSessionTurnQueueItem

_SESSION_ACTIVITY_BATCH_SIZE = 200


@dataclass(frozen=True, slots=True)
class ChatSessionTurnQueueActivity:
    """Aggregated live queue activity for one `(profile_id, session_id)` pair."""

    profile_id: str
    session_id: str
    queued_turn_count: int
    running_turn_count: int
    latest_activity_at: datetime | None


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

    async def list_session_activity(
        self,
        *,
        session_keys: Sequence[tuple[str, str]],
        older_than: datetime,
    ) -> list[ChatSessionTurnQueueActivity]:
        """Return non-stale queue activity grouped by profile/session key."""

        normalized_keys = tuple(
            dict.fromkeys(
                (
                    str(profile_id).strip(),
                    str(session_id).strip(),
                )
                for profile_id, session_id in session_keys
                if str(profile_id).strip() and str(session_id).strip()
            )
        )
        if not normalized_keys:
            return []

        activities: list[ChatSessionTurnQueueActivity] = []
        for start in range(0, len(normalized_keys), _SESSION_ACTIVITY_BATCH_SIZE):
            batch = normalized_keys[start : start + _SESSION_ACTIVITY_BATCH_SIZE]
            filters = [
                and_(
                    ChatSessionTurnQueueItem.profile_id == profile_id,
                    ChatSessionTurnQueueItem.session_id == session_id,
                )
                for profile_id, session_id in batch
            ]
            statement = (
                select(
                    ChatSessionTurnQueueItem.profile_id,
                    ChatSessionTurnQueueItem.session_id,
                    func.sum(
                        case((ChatSessionTurnQueueItem.status == "queued", 1), else_=0)
                    ).label("queued_turn_count"),
                    func.sum(
                        case((ChatSessionTurnQueueItem.status == "running", 1), else_=0)
                    ).label("running_turn_count"),
                    func.max(ChatSessionTurnQueueItem.updated_at).label("latest_activity_at"),
                )
                .where(
                    or_(*filters),
                    ChatSessionTurnQueueItem.updated_at > older_than,
                )
                .group_by(
                    ChatSessionTurnQueueItem.profile_id,
                    ChatSessionTurnQueueItem.session_id,
                )
            )
            rows = (await self._session.execute(statement)).all()
            activities.extend(
                ChatSessionTurnQueueActivity(
                    profile_id=str(profile_id),
                    session_id=str(session_id),
                    queued_turn_count=int(queued_turn_count or 0),
                    running_turn_count=int(running_turn_count or 0),
                    latest_activity_at=latest_activity_at,
                )
                for profile_id, session_id, queued_turn_count, running_turn_count, latest_activity_at in rows
            )
        return activities
