"""Repository for chat-turn idempotency rows and execution claims."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import Select, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.chat_turn_idempotency import (
    ChatTurnIdempotency,
    ChatTurnIdempotencyClaim,
)


class ChatTurnIdempotencyRepository:
    """Persistence operations for API turn idempotency keys."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_key(
        self,
        *,
        profile_id: str,
        session_id: str,
        client_msg_id: str,
    ) -> ChatTurnIdempotency | None:
        """Load one stored idempotent turn by natural key."""

        statement: Select[tuple[ChatTurnIdempotency]] = select(ChatTurnIdempotency).where(
            ChatTurnIdempotency.profile_id == profile_id,
            ChatTurnIdempotency.session_id == session_id,
            ChatTurnIdempotency.client_msg_id == client_msg_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def create(
        self,
        *,
        profile_id: str,
        session_id: str,
        client_msg_id: str,
        run_id: int,
        envelope_json: str,
    ) -> ChatTurnIdempotency | None:
        """Create idempotency row; return None when duplicate key already exists."""

        row = ChatTurnIdempotency(
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
            run_id=run_id,
            envelope_json=envelope_json,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            return None
        await self._session.refresh(row)
        return row

    async def try_claim(
        self,
        *,
        profile_id: str,
        session_id: str,
        client_msg_id: str,
        owner_token: str,
    ) -> bool:
        """Attempt to claim key ownership for one in-flight turn execution."""

        row = ChatTurnIdempotencyClaim(
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
            owner_token=owner_token,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            return False
        return True

    async def has_claim(
        self,
        *,
        profile_id: str,
        session_id: str,
        client_msg_id: str,
    ) -> bool:
        """Return true when key is currently claimed by some in-flight request."""

        statement: Select[tuple[int]] = (
            select(ChatTurnIdempotencyClaim.id)
            .where(
                ChatTurnIdempotencyClaim.profile_id == profile_id,
                ChatTurnIdempotencyClaim.session_id == session_id,
                ChatTurnIdempotencyClaim.client_msg_id == client_msg_id,
            )
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none() is not None

    async def release_claim(
        self,
        *,
        profile_id: str,
        session_id: str,
        client_msg_id: str,
        owner_token: str,
    ) -> None:
        """Release ownership claim created by this request, if it still exists."""

        await self._session.execute(
            delete(ChatTurnIdempotencyClaim).where(
                ChatTurnIdempotencyClaim.profile_id == profile_id,
                ChatTurnIdempotencyClaim.session_id == session_id,
                ChatTurnIdempotencyClaim.client_msg_id == client_msg_id,
                ChatTurnIdempotencyClaim.owner_token == owner_token,
            )
        )

    async def release_stale_claims(
        self,
        *,
        profile_id: str,
        session_id: str,
        client_msg_id: str,
        older_than: datetime,
    ) -> int:
        """Delete claims whose last heartbeat is older than the provided cutoff."""

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                delete(ChatTurnIdempotencyClaim).where(
                    ChatTurnIdempotencyClaim.profile_id == profile_id,
                    ChatTurnIdempotencyClaim.session_id == session_id,
                    ChatTurnIdempotencyClaim.client_msg_id == client_msg_id,
                    ChatTurnIdempotencyClaim.updated_at <= older_than,
                )
            ),
        )
        return int(result.rowcount or 0)

    async def touch_claim(
        self,
        *,
        profile_id: str,
        session_id: str,
        client_msg_id: str,
        owner_token: str,
        touched_at: datetime,
    ) -> bool:
        """Refresh one in-flight claim timestamp when still owned by this request."""

        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(ChatTurnIdempotencyClaim)
                .where(
                    ChatTurnIdempotencyClaim.profile_id == profile_id,
                    ChatTurnIdempotencyClaim.session_id == session_id,
                    ChatTurnIdempotencyClaim.client_msg_id == client_msg_id,
                    ChatTurnIdempotencyClaim.owner_token == owner_token,
                )
                .values(updated_at=touched_at)
            ),
        )
        return int(result.rowcount or 0) > 0
