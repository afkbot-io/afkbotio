"""Repository for pinned profile/core memory rows."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.profile_memory_item import ProfileMemoryItem
from afkbot.repositories.support import profile_exists


class ProfileMemoryRepository:
    """Persistence operations for durable profile/core memory."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_item(
        self,
        *,
        profile_id: str,
        memory_key: str,
        content: str,
        summary: str | None,
        details_md: str | None,
        source: str | None,
        source_kind: str,
        memory_kind: str,
        priority: int,
        confidence: float,
        last_confirmed_at: datetime | None,
        stale: bool,
    ) -> ProfileMemoryItem:
        """Create or update one profile-memory row by logical key."""

        existing = await self.get_by_key(profile_id=profile_id, memory_key=memory_key)
        if existing is None:
            item = ProfileMemoryItem(
                profile_id=profile_id,
                memory_key=memory_key,
                content=content,
                summary=summary,
                details_md=details_md,
                source=source,
                source_kind=source_kind,
                memory_kind=memory_kind,
                priority=priority,
                confidence=confidence,
                last_confirmed_at=last_confirmed_at,
                stale=stale,
            )
            self._session.add(item)
            await self._session.flush()
            await self._session.refresh(item)
            return item

        existing.content = content
        existing.summary = summary
        existing.details_md = details_md
        existing.source = source
        existing.source_kind = source_kind
        existing.memory_kind = memory_kind
        existing.priority = priority
        existing.confidence = confidence
        existing.last_confirmed_at = last_confirmed_at
        existing.stale = stale
        await self._session.flush()
        await self._session.refresh(existing)
        return existing

    async def get_by_key(
        self,
        *,
        profile_id: str,
        memory_key: str,
    ) -> ProfileMemoryItem | None:
        """Return one profile-memory row by logical key."""

        statement: Select[tuple[ProfileMemoryItem]] = select(ProfileMemoryItem).where(
            ProfileMemoryItem.profile_id == profile_id,
            func.lower(ProfileMemoryItem.memory_key) == memory_key.lower(),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_by_profile(
        self,
        *,
        profile_id: str,
        include_stale: bool = False,
        limit: int | None = 50,
    ) -> list[ProfileMemoryItem]:
        """List profile-memory rows ordered by durable quality."""

        statement: Select[tuple[ProfileMemoryItem]] = select(ProfileMemoryItem).where(
            ProfileMemoryItem.profile_id == profile_id
        )
        if not include_stale:
            statement = statement.where(ProfileMemoryItem.stale.is_(False))
        statement = statement.order_by(
            ProfileMemoryItem.priority.desc(),
            ProfileMemoryItem.confidence.desc(),
            ProfileMemoryItem.last_confirmed_at.desc().nulls_last(),
            ProfileMemoryItem.updated_at.desc(),
            ProfileMemoryItem.id.desc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def mark_stale(
        self,
        *,
        profile_id: str,
        memory_key: str,
        stale: bool,
    ) -> ProfileMemoryItem | None:
        """Update stale flag for one logical key."""

        row = await self.get_by_key(profile_id=profile_id, memory_key=memory_key)
        if row is None:
            return None
        row.stale = stale
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def delete_by_key(self, *, profile_id: str, memory_key: str) -> bool:
        """Delete one profile-memory row by logical key."""

        row = await self.get_by_key(profile_id=profile_id, memory_key=memory_key)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def validate_profile_exists(self, profile_id: str) -> bool:
        """Return True when profile exists in storage."""

        return await profile_exists(self._session, profile_id=profile_id)
