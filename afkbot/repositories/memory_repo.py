"""Repository for scoped semantic memory rows on SQLite-backed storage."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.memory_item import MemoryItem
from afkbot.repositories.memory_search import (
    MemoryRankCandidate,
    normalize_embedding,
    rank_memory_candidates,
)
from afkbot.repositories.support import profile_exists

MemorySourceKind = Literal["manual", "auto", "watcher", "automation"]
MemoryKind = Literal["fact", "preference", "decision", "task", "risk", "note"]
MemoryVisibility = Literal["local", "promoted_global"]


class MemoryRepository:
    """Persistence operations for scoped semantic memory items."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_item(
        self,
        *,
        profile_id: str,
        storage_key: str,
        logical_key: str,
        content: str,
        summary: str | None,
        details_md: str | None,
        source: str | None,
        scope_key: str,
        scope_kind: str,
        transport: str | None,
        account_id: str | None,
        peer_id: str | None,
        thread_id: str | None,
        user_id: str | None,
        session_id: str | None,
        binding_id: str | None,
        source_kind: MemorySourceKind,
        memory_kind: MemoryKind,
        visibility: MemoryVisibility,
        embedding: Sequence[float],
    ) -> MemoryItem:
        """Create or update one scoped memory row by logical key inside one scope."""

        normalized_embedding = normalize_embedding(embedding)
        if normalized_embedding is None:
            raise ValueError("embedding must be a non-empty finite numeric sequence")
        existing = await self.get_by_storage_key(profile_id=profile_id, storage_key=storage_key)
        if existing is None:
            item = MemoryItem(
                profile_id=profile_id,
                memory_key=storage_key,
                logical_key=logical_key,
                content=content,
                summary=summary,
                details_md=details_md,
                source=source,
                scope_key=scope_key,
                scope_kind=scope_kind,
                transport=transport,
                account_id=account_id,
                peer_id=peer_id,
                thread_id=thread_id,
                user_id=user_id,
                session_id=session_id,
                binding_id=binding_id,
                source_kind=source_kind,
                memory_kind=memory_kind,
                visibility=visibility,
                embedding=list(normalized_embedding),
            )
            self._session.add(item)
            await self._session.flush()
            await self._session.refresh(item)
            return item

        existing.logical_key = logical_key
        existing.content = content
        existing.summary = summary
        existing.details_md = details_md
        existing.source = source
        existing.scope_key = scope_key
        existing.scope_kind = scope_kind
        existing.transport = transport
        existing.account_id = account_id
        existing.peer_id = peer_id
        existing.thread_id = thread_id
        existing.user_id = user_id
        existing.session_id = session_id
        existing.binding_id = binding_id
        existing.source_kind = source_kind
        existing.memory_kind = memory_kind
        existing.visibility = visibility
        existing.embedding = list(normalized_embedding)
        await self._session.flush()
        await self._session.refresh(existing)
        return existing

    async def delete_by_storage_key(self, *, profile_id: str, storage_key: str) -> bool:
        """Delete one memory row by internal storage key; return False when missing."""

        row = await self.get_by_storage_key(profile_id=profile_id, storage_key=storage_key)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def get_by_storage_key(self, *, profile_id: str, storage_key: str) -> MemoryItem | None:
        """Return one memory row for profile/storage-key."""

        statement: Select[tuple[MemoryItem]] = select(MemoryItem).where(
            MemoryItem.profile_id == profile_id,
            MemoryItem.memory_key == storage_key,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_by_profile(
        self,
        *,
        profile_id: str,
        scope_key: str | None = None,
        visibility: MemoryVisibility | None = None,
        limit: int | None = 200,
    ) -> list[MemoryItem]:
        """List memory items in deterministic updated order with optional scope filtering."""

        statement: Select[tuple[MemoryItem]] = select(MemoryItem).where(
            MemoryItem.profile_id == profile_id
        )
        if scope_key is not None:
            statement = statement.where(MemoryItem.scope_key == scope_key)
        if visibility is not None:
            statement = statement.where(MemoryItem.visibility == visibility)
        statement = statement.order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc())
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def list_profile_ids(self) -> list[str]:
        """Return distinct profile ids that currently own memory rows."""

        statement = select(MemoryItem.profile_id).distinct()
        result = await self._session.execute(statement)
        return [str(value) for value in result.scalars().all()]

    async def delete_older_than(
        self,
        *,
        profile_id: str,
        cutoff: datetime,
        batch_size: int | None = None,
    ) -> int:
        """Delete profile memory rows older than cutoff by updated timestamp."""

        stale_ids_statement = (
            select(MemoryItem.id)
            .where(
                MemoryItem.profile_id == profile_id,
                MemoryItem.updated_at < cutoff,
            )
            .order_by(MemoryItem.updated_at.asc(), MemoryItem.id.asc())
        )
        if batch_size is not None and batch_size > 0:
            stale_ids_statement = stale_ids_statement.limit(batch_size)
        stale_ids = list((await self._session.execute(stale_ids_statement)).scalars().all())
        if not stale_ids:
            return 0
        statement = (
            delete(MemoryItem)
            .where(MemoryItem.id.in_(stale_ids))
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(statement)
        await self._session.flush()
        return len(stale_ids)

    async def trim_to_limit(
        self,
        *,
        profile_id: str,
        limit: int,
        batch_size: int | None = None,
    ) -> int:
        """Trim profile memory to keep only latest `limit` rows across scopes."""

        overflow_statement = (
            select(MemoryItem.id)
            .where(MemoryItem.profile_id == profile_id)
            .order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc())
            .offset(limit)
        )
        if batch_size is not None and batch_size > 0:
            overflow_statement = overflow_statement.limit(batch_size)
        overflow_ids = list((await self._session.execute(overflow_statement)).scalars().all())
        if not overflow_ids:
            return 0
        statement = (
            delete(MemoryItem)
            .where(MemoryItem.id.in_(overflow_ids))
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(statement)
        await self._session.flush()
        return len(overflow_ids)

    async def search_by_embedding(
        self,
        *,
        profile_id: str,
        query_embedding: Sequence[float],
        scope_key: str | None = None,
        visibility: MemoryVisibility | None = None,
        memory_kinds: Sequence[MemoryKind] | None = None,
        source_kinds: Sequence[MemorySourceKind] | None = None,
        limit: int = 5,
    ) -> list[tuple[MemoryItem, float]]:
        """Return nearest memory rows with cosine distance (lower is better)."""

        normalized_query = normalize_embedding(query_embedding)
        if normalized_query is None:
            return []
        candidate_statement = select(
            MemoryItem.id,
            MemoryItem.embedding,
            MemoryItem.memory_kind,
            MemoryItem.source_kind,
        ).where(MemoryItem.profile_id == profile_id)
        if scope_key is not None:
            candidate_statement = candidate_statement.where(MemoryItem.scope_key == scope_key)
        if visibility is not None:
            candidate_statement = candidate_statement.where(MemoryItem.visibility == visibility)
        candidate_statement = candidate_statement.order_by(
            MemoryItem.updated_at.desc(),
            MemoryItem.id.desc(),
        )
        candidate_rows = (await self._session.execute(candidate_statement)).all()
        ranked_candidates = rank_memory_candidates(
            items=[
                MemoryRankCandidate(
                    item_id=int(item_id),
                    embedding=embedding,
                    memory_kind=str(memory_kind),
                    source_kind=str(source_kind),
                )
                for item_id, embedding, memory_kind, source_kind in candidate_rows
            ],
            query_embedding=normalized_query,
            limit=limit,
            allowed_memory_kinds=set(memory_kinds) if memory_kinds else None,
            allowed_source_kinds=set(source_kinds) if source_kinds else None,
        )
        if not ranked_candidates:
            return []

        ranked_ids = [candidate.item_id for candidate, _ in ranked_candidates]
        item_statement: Select[tuple[MemoryItem]] = select(MemoryItem).where(
            MemoryItem.id.in_(ranked_ids)
        )
        loaded_items = (await self._session.execute(item_statement)).scalars().all()
        items_by_id = {item.id: item for item in loaded_items}
        return [
            (items_by_id[candidate.item_id], score)
            for candidate, score in ranked_candidates
            if candidate.item_id in items_by_id
        ]

    async def validate_profile_exists(self, profile_id: str) -> bool:
        """Return True when profile exists in storage."""

        return await profile_exists(self._session, profile_id=profile_id)
