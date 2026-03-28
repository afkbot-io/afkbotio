"""Repository for persisted session compaction summaries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.chat_session_compaction import ChatSessionCompaction


class ChatSessionCompactionRepository:
    """Load and upsert trusted compaction summaries per session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self,
        *,
        profile_id: str,
        session_id: str,
    ) -> ChatSessionCompaction | None:
        """Return current compaction record for one session."""

        statement = select(ChatSessionCompaction).where(
            ChatSessionCompaction.profile_id == profile_id,
            ChatSessionCompaction.session_id == session_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def upsert(
        self,
        *,
        profile_id: str,
        session_id: str,
        summary_text: str,
        compacted_until_turn_id: int,
        source_turn_count: int,
        strategy: str,
    ) -> ChatSessionCompaction:
        """Create or update one compaction record atomically."""

        payload = {
            "profile_id": profile_id,
            "session_id": session_id,
            "summary_text": summary_text,
            "compacted_until_turn_id": compacted_until_turn_id,
            "source_turn_count": source_turn_count,
            "strategy": strategy,
        }
        statement = sqlite_insert(ChatSessionCompaction).values(**payload)
        statement = statement.on_conflict_do_update(
            index_elements=[
                ChatSessionCompaction.session_id,
                ChatSessionCompaction.profile_id,
            ],
            set_={
                "summary_text": payload["summary_text"],
                "compacted_until_turn_id": payload["compacted_until_turn_id"],
                "source_turn_count": payload["source_turn_count"],
                "strategy": payload["strategy"],
            },
        )
        await self._session.execute(statement)
        await self._session.flush()
        row = await self.get(profile_id=profile_id, session_id=session_id)
        if row is None:
            raise RuntimeError("Failed to load chat session compaction after upsert")
        return row

    async def list_prunable(
        self,
        *,
        limit: int,
        profile_id: str | None = None,
        session_id: str | None = None,
    ) -> list[ChatSessionCompaction]:
        """Return compacted sessions that still have raw turns eligible for GC."""

        if limit < 1:
            return []
        statement = select(ChatSessionCompaction).where(
            ChatSessionCompaction.compacted_until_turn_id > 0
        )
        if profile_id is not None:
            statement = statement.where(ChatSessionCompaction.profile_id == profile_id)
        if session_id is not None:
            statement = statement.where(ChatSessionCompaction.session_id == session_id)
        statement = statement.order_by(
            ChatSessionCompaction.updated_at.asc(),
            ChatSessionCompaction.session_id.asc(),
        ).limit(limit)
        return list((await self._session.execute(statement)).scalars().all())
