"""Memory service orchestration over scoped semantic-memory storage."""

from __future__ import annotations

import builtins
import hashlib
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.memory_item import MemoryItem
from afkbot.repositories.memory_repo import MemoryRepository
from afkbot.services.memory.contracts import (
    MemoryItemMetadata,
    MemoryKind,
    MemoryScopeDescriptor,
    MemorySourceKind,
    MemoryVisibility,
)
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "MemoryService"] = {}
TValue = TypeVar("TValue")


class MemoryServiceError(Exception):
    """Application error for memory service methods."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class MemoryService:
    """Service for scoped semantic memory CRUD, search, and promotion."""

    _EMBEDDING_DIM = 64

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        engine: AsyncEngine | None = None,
        retention_days: int = 90,
        max_items_per_profile: int = 2000,
        gc_batch_size: int = 500,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._retention_days = max(0, retention_days)
        self._max_items_per_profile = max(0, max_items_per_profile)
        self._gc_batch_size = max(1, gc_batch_size)

    async def upsert(
        self,
        *,
        profile_id: str,
        memory_key: str,
        content: str | None = None,
        summary: str | None = None,
        details_md: str | None = None,
        source: str | None = None,
        scope: MemoryScopeDescriptor | None = None,
        source_kind: MemorySourceKind = "manual",
        memory_kind: MemoryKind = "note",
        visibility: MemoryVisibility | None = None,
    ) -> MemoryItemMetadata:
        """Create or update one scoped memory item for profile and logical key."""

        normalized_scope = scope or MemoryScopeDescriptor.profile_scope()
        normalized_key = self._normalize_memory_key(memory_key)
        normalized_source = self._normalize_source(source)
        normalized_summary = self._normalize_optional_text(summary)
        normalized_details = self._normalize_optional_text(details_md)
        normalized_content = self._normalize_semantic_content(
            content=content,
            summary=normalized_summary,
            details_md=normalized_details,
        )
        summary_text = normalized_summary or self._derive_summary(normalized_content)
        effective_visibility = visibility or (
            "promoted_global" if normalized_scope.is_profile_scope else "local"
        )
        embedding = self._embed_text(normalized_content)
        storage_key = normalized_scope.storage_key(normalized_key)

        async def _op(repo: MemoryRepository) -> MemoryItemMetadata:
            await self._ensure_profile_exists(repo, profile_id)
            row = await repo.upsert_item(
                profile_id=profile_id,
                storage_key=storage_key,
                logical_key=normalized_key,
                content=normalized_content,
                summary=summary_text,
                details_md=normalized_details,
                source=normalized_source,
                scope_key=normalized_scope.scope_key(),
                scope_kind=normalized_scope.scope_kind,
                transport=normalized_scope.transport,
                account_id=normalized_scope.account_id,
                peer_id=normalized_scope.peer_id,
                thread_id=normalized_scope.thread_id,
                user_id=normalized_scope.user_id,
                session_id=normalized_scope.session_id,
                binding_id=normalized_scope.binding_id,
                source_kind=source_kind,
                memory_kind=memory_kind,
                visibility=effective_visibility,
                embedding=embedding,
            )
            await self._collect_profile_garbage(repo, profile_id=profile_id)
            return self._to_metadata(row=row, score=None)

        return await self._with_repo(_op)

    async def delete(
        self,
        *,
        profile_id: str,
        memory_key: str,
        scope: MemoryScopeDescriptor | None = None,
    ) -> bool:
        """Delete one memory item by logical key within one scope."""

        normalized_scope = scope or MemoryScopeDescriptor.profile_scope()
        normalized_key = self._normalize_memory_key(memory_key)
        storage_key = normalized_scope.storage_key(normalized_key)

        async def _op(repo: MemoryRepository) -> bool:
            await self._ensure_profile_exists(repo, profile_id)
            deleted = await repo.delete_by_storage_key(
                profile_id=profile_id,
                storage_key=storage_key,
            )
            if not deleted:
                raise MemoryServiceError(
                    error_code="memory_not_found",
                    reason="Memory item not found",
                )
            return True

        return await self._with_repo(_op)

    async def get(
        self,
        *,
        profile_id: str,
        memory_key: str,
        scope: MemoryScopeDescriptor | None = None,
    ) -> MemoryItemMetadata:
        """Return one memory item by logical key and scope."""

        normalized_scope = scope or MemoryScopeDescriptor.profile_scope()
        normalized_key = self._normalize_memory_key(memory_key)
        storage_key = normalized_scope.storage_key(normalized_key)

        async def _op(repo: MemoryRepository) -> MemoryItemMetadata:
            await self._ensure_profile_exists(repo, profile_id)
            row = await repo.get_by_storage_key(profile_id=profile_id, storage_key=storage_key)
            if row is None:
                raise MemoryServiceError(
                    error_code="memory_not_found",
                    reason="Memory item not found",
                )
            return self._to_metadata(row=row, score=None)

        return await self._with_repo(_op)

    async def list(
        self,
        *,
        profile_id: str,
        scope: MemoryScopeDescriptor | None = None,
        visibility: MemoryVisibility | None = None,
        limit: int = 50,
    ) -> list[MemoryItemMetadata]:
        """List scoped or whole-profile memory items in recency order."""

        normalized_limit = self._normalize_list_limit(limit)
        normalized_scope_key = None if scope is None else scope.scope_key()

        async def _op(repo: MemoryRepository) -> list[MemoryItemMetadata]:
            await self._ensure_profile_exists(repo, profile_id)
            rows = await repo.list_by_profile(
                profile_id=profile_id,
                scope_key=normalized_scope_key,
                visibility=visibility,
                limit=normalized_limit,
            )
            return [self._to_metadata(row=row, score=None) for row in rows]

        return await self._with_repo(_op)

    async def list_profiles(self) -> builtins.list[str]:
        """List profile ids that currently own at least one memory row."""

        async def _op(repo: MemoryRepository) -> builtins.list[str]:
            return await repo.list_profile_ids()

        return await self._with_repo(_op)

    async def search(
        self,
        *,
        profile_id: str,
        query: str,
        scope: MemoryScopeDescriptor | None = None,
        visibility: MemoryVisibility | None = None,
        memory_kinds: Sequence[MemoryKind] | None = None,
        source_kinds: Sequence[MemorySourceKind] | None = None,
        limit: int = 5,
    ) -> builtins.list[MemoryItemMetadata]:
        """Search nearest scoped memory items by semantic embedding distance."""

        normalized_query = self._normalize_semantic_content(
            content=query, summary=None, details_md=None
        )
        normalized_limit = self._normalize_limit(limit)
        query_embedding = self._embed_text(normalized_query)
        normalized_scope_key = None if scope is None else scope.scope_key()
        normalized_memory_kinds = None if memory_kinds is None else tuple(memory_kinds)
        normalized_source_kinds = None if source_kinds is None else tuple(source_kinds)

        async def _op(repo: MemoryRepository) -> builtins.list[MemoryItemMetadata]:
            await self._ensure_profile_exists(repo, profile_id)
            rows = await repo.search_by_embedding(
                profile_id=profile_id,
                query_embedding=query_embedding,
                scope_key=normalized_scope_key,
                visibility=visibility,
                memory_kinds=normalized_memory_kinds,
                source_kinds=normalized_source_kinds,
                limit=normalized_limit,
            )
            return [self._to_metadata(row=row, score=distance) for row, distance in rows]

        return await self._with_repo(_op)

    async def promote(
        self,
        *,
        profile_id: str,
        memory_key: str,
        from_scope: MemoryScopeDescriptor,
        target_memory_key: str | None = None,
    ) -> MemoryItemMetadata:
        """Promote one local memory item into profile-global memory."""

        source_item = await self.get(profile_id=profile_id, memory_key=memory_key, scope=from_scope)
        if source_item.scope_kind == "profile":
            return source_item
        promoted_key = self._normalize_memory_key(target_memory_key or source_item.memory_key)
        return await self.upsert(
            profile_id=profile_id,
            memory_key=promoted_key,
            content=source_item.content,
            summary=source_item.summary,
            details_md=source_item.details_md,
            source=source_item.source,
            scope=MemoryScopeDescriptor.profile_scope(
                session_id=source_item.session_id,
                binding_id=source_item.binding_id,
            ),
            source_kind=source_item.source_kind,
            memory_kind=source_item.memory_kind,
            visibility="promoted_global",
        )

    async def shutdown(self) -> None:
        """Dispose owned async engine when the service created it."""

        if self._engine is None:
            return
        await self._engine.dispose()

    async def garbage_collect(self, *, profile_id: str | None = None) -> int:
        """Apply retention/overflow limits and return number of removed rows."""

        async def _op(repo: MemoryRepository) -> int:
            if profile_id is not None:
                await self._ensure_profile_exists(repo, profile_id)
                return await self._collect_profile_garbage(repo, profile_id=profile_id)
            total_deleted = 0
            for current_profile_id in await repo.list_profile_ids():
                total_deleted += await self._collect_profile_garbage(
                    repo, profile_id=current_profile_id
                )
            return total_deleted

        return await self._with_repo(_op)

    async def _with_repo(
        self,
        op: Callable[[MemoryRepository], Awaitable[TValue]],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            repo = MemoryRepository(session)
            return await op(repo)

    async def _ensure_profile_exists(self, repo: MemoryRepository, profile_id: str) -> None:
        if await repo.validate_profile_exists(profile_id):
            return
        raise MemoryServiceError(error_code="profile_not_found", reason="Profile not found")

    async def _collect_profile_garbage(self, repo: MemoryRepository, *, profile_id: str) -> int:
        deleted = 0
        if self._retention_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
            while True:
                removed = await repo.delete_older_than(
                    profile_id=profile_id,
                    cutoff=cutoff,
                    batch_size=self._gc_batch_size,
                )
                deleted += removed
                if removed < self._gc_batch_size:
                    break
        if self._max_items_per_profile > 0:
            while True:
                removed = await repo.trim_to_limit(
                    profile_id=profile_id,
                    limit=self._max_items_per_profile,
                    batch_size=self._gc_batch_size,
                )
                deleted += removed
                if removed < self._gc_batch_size:
                    break
        return deleted

    @staticmethod
    def _normalize_memory_key(memory_key: str) -> str:
        value = memory_key.strip()
        if not value:
            raise MemoryServiceError(
                error_code="memory_invalid_key", reason="Memory key is required"
            )
        if len(value) > 128:
            raise MemoryServiceError(
                error_code="memory_invalid_key",
                reason="Memory key is too long",
            )
        return value

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @classmethod
    def _normalize_semantic_content(
        cls,
        *,
        content: str | None,
        summary: str | None,
        details_md: str | None,
    ) -> str:
        raw_content = cls._normalize_optional_text(content)
        if raw_content is not None:
            return raw_content
        if summary and details_md:
            return f"{summary}\n\n{details_md}"
        if summary:
            return summary
        if details_md:
            return details_md
        raise MemoryServiceError(error_code="memory_invalid_content", reason="Content is required")

    @staticmethod
    def _derive_summary(content: str) -> str:
        single_line = " ".join(content.split())
        if len(single_line) <= 180:
            return single_line
        return single_line[:177].rstrip() + "..."

    @staticmethod
    def _normalize_source(source: str | None) -> str | None:
        if source is None:
            return None
        value = source.strip()
        if len(value) > 128:
            raise MemoryServiceError(
                error_code="memory_invalid_source",
                reason="Source is too long",
            )
        return value or None

    @staticmethod
    def _normalize_limit(limit: int) -> int:
        if limit < 1:
            raise MemoryServiceError(error_code="memory_invalid_limit", reason="limit must be >= 1")
        return min(limit, 20)

    @staticmethod
    def _normalize_list_limit(limit: int) -> int:
        if limit < 1:
            raise MemoryServiceError(error_code="memory_invalid_limit", reason="limit must be >= 1")
        return min(limit, 500)

    @classmethod
    def _embed_text(cls, text: str) -> builtins.list[float]:
        """Build deterministic local embedding vector for one text snippet."""

        vector = [0.0] * cls._EMBEDDING_DIM
        words = text.lower().split()
        if not words:
            return vector
        for word in words:
            digest = hashlib.sha256(word.encode("utf-8")).digest()
            for idx in range(cls._EMBEDDING_DIM):
                vector[idx] += (digest[idx % len(digest)] / 255.0) - 0.5
        scale = float(len(words))
        return [value / scale for value in vector]

    @staticmethod
    def _to_metadata(*, row: MemoryItem, score: float | None) -> MemoryItemMetadata:
        return MemoryItemMetadata(
            id=row.id,
            profile_id=row.profile_id,
            memory_key=row.logical_key,
            scope_kind=row.scope_kind,  # type: ignore[arg-type]
            scope_key=row.scope_key,
            transport=row.transport,
            account_id=row.account_id,
            peer_id=row.peer_id,
            thread_id=row.thread_id,
            user_id=row.user_id,
            session_id=row.session_id,
            binding_id=row.binding_id,
            source=row.source,
            source_kind=row.source_kind,  # type: ignore[arg-type]
            memory_kind=row.memory_kind,  # type: ignore[arg-type]
            visibility=row.visibility,  # type: ignore[arg-type]
            summary=row.summary,
            details_md=row.details_md,
            content=row.content,
            score=score,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def get_memory_service(settings: Settings) -> MemoryService:
    """Get or create one memory service for current root directory."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is not None:
        return service
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    service = MemoryService(
        factory,
        engine=engine,
        retention_days=settings.memory_retention_days,
        max_items_per_profile=settings.memory_max_items_per_profile,
        gc_batch_size=settings.memory_gc_batch_size,
    )
    _SERVICES_BY_ROOT[key] = service
    return service


def reset_memory_services() -> None:
    """Reset cached memory service instances for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_memory_services_async() -> None:
    """Reset cached memory services and dispose owned async engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
