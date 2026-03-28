"""Read-oriented repository for persisted chat turns."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.chat_turn import ChatTurn


class ChatTurnRepository:
    """Load persisted chat turns for one profile/session pair."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def count(self, *, profile_id: str, session_id: str) -> int:
        """Return total number of turns for one session."""

        statement = select(func.count(ChatTurn.id)).where(
            ChatTurn.profile_id == profile_id,
            ChatTurn.session_id == session_id,
        )
        value = (await self._session.execute(statement)).scalar_one()
        return int(value or 0)

    async def list_recent(
        self,
        *,
        profile_id: str,
        session_id: str,
        limit: int,
        min_turn_id_exclusive: int = 0,
    ) -> list[ChatTurn]:
        """Return recent turns in chronological order."""

        if limit < 1:
            return []
        statement = (
            select(ChatTurn)
            .where(
                ChatTurn.profile_id == profile_id,
                ChatTurn.session_id == session_id,
                ChatTurn.id > min_turn_id_exclusive,
            )
            .order_by(ChatTurn.id.desc())
            .limit(limit)
        )
        rows = list((await self._session.execute(statement)).scalars().all())
        rows.reverse()
        return rows

    async def list_range(
        self,
        *,
        profile_id: str,
        session_id: str,
        min_turn_id_exclusive: int = 0,
        max_turn_id_exclusive: int | None = None,
    ) -> list[ChatTurn]:
        """Return turns in ascending order within one id window."""

        statement = select(ChatTurn).where(
            ChatTurn.profile_id == profile_id,
            ChatTurn.session_id == session_id,
            ChatTurn.id > min_turn_id_exclusive,
        )
        if max_turn_id_exclusive is not None:
            statement = statement.where(ChatTurn.id < max_turn_id_exclusive)
        statement = statement.order_by(ChatTurn.id.asc())
        return list((await self._session.execute(statement)).scalars().all())

    async def delete_up_to(
        self,
        *,
        profile_id: str,
        session_id: str,
        max_turn_id_inclusive: int,
        limit: int | None = None,
    ) -> int:
        """Delete older persisted turns up to provided inclusive boundary."""

        if max_turn_id_inclusive < 1:
            return 0
        if limit is None:
            statement = delete(ChatTurn).where(
                ChatTurn.profile_id == profile_id,
                ChatTurn.session_id == session_id,
                ChatTurn.id <= max_turn_id_inclusive,
            )
            result = cast(CursorResult[Any], await self._session.execute(statement))
            return int(result.rowcount or 0)
        candidate_ids = (
            select(ChatTurn.id)
            .where(
                ChatTurn.profile_id == profile_id,
                ChatTurn.session_id == session_id,
                ChatTurn.id <= max_turn_id_inclusive,
            )
            .order_by(ChatTurn.id.asc())
            .limit(max(1, limit))
        )
        statement = delete(ChatTurn).where(ChatTurn.id.in_(candidate_ids))
        result = cast(CursorResult[Any], await self._session.execute(statement))
        return int(result.rowcount or 0)
