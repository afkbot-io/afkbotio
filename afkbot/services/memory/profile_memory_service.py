"""Service for durable pinned profile/core memory."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import TypeVar

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.profile_memory_item import ProfileMemoryItem
from afkbot.repositories.profile_memory_repo import ProfileMemoryRepository
from afkbot.services.memory.contracts import MemoryKind
from afkbot.settings import Settings

ProfileMemorySourceKind = str
TValue = TypeVar("TValue")
_SERVICES_BY_ROOT: dict[str, "ProfileMemoryService"] = {}
_SECRET_PATTERN = re.compile(r"(api[_ -]?key|token|password|secret|sk-[a-z0-9_-]{8,})", re.IGNORECASE)
_VOLATILE_PATTERN = re.compile(
    r"(for this chat only|in this chat only|this session|temporary|temporarily|for now only|только в этом чате|на эту сессию|временно)",
    re.IGNORECASE,
)


class ProfileMemoryServiceError(Exception):
    """Application error for profile/core memory methods."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ProfileMemoryPromptBlock:
    """Rendered core-memory prompt block."""

    content: str
    included_keys: list[str]
    truncated: bool


@dataclass(frozen=True, slots=True)
class ProfileMemoryItemMetadata:
    """Serialized profile/core memory item."""

    id: int
    profile_id: str
    memory_key: str
    source: str | None
    source_kind: str
    memory_kind: str
    priority: int
    confidence: float
    stale: bool
    summary: str | None
    details_md: str | None
    content: str
    last_confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProfileMemoryService:
    """CRUD, filtering, and prompt rendering for pinned profile memory."""

    _DEFAULT_MIN_CONFIDENCE = 0.5

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine

    async def upsert(
        self,
        *,
        profile_id: str,
        memory_key: str,
        content: str | None = None,
        summary: str | None = None,
        details_md: str | None = None,
        source: str | None = None,
        source_kind: ProfileMemorySourceKind = "manual",
        memory_kind: MemoryKind = "fact",
        priority: int = 50,
        confidence: float = 0.8,
        last_confirmed_at: datetime | None = None,
        stale: bool = False,
    ) -> ProfileMemoryItemMetadata:
        """Create or update one durable profile-memory item."""

        normalized_key = self._normalize_memory_key(memory_key)
        normalized_summary = self._normalize_optional_text(summary)
        normalized_details = self._normalize_optional_text(details_md)
        normalized_content = self._normalize_content(
            content=content,
            summary=normalized_summary,
            details_md=normalized_details,
        )
        normalized_source = self._normalize_optional_text(source)
        normalized_priority = self._normalize_priority(priority)
        normalized_confidence = self._normalize_confidence(confidence)
        confirmed_at = last_confirmed_at or datetime.now(timezone.utc)

        async def _op(repo: ProfileMemoryRepository) -> ProfileMemoryItemMetadata:
            await self._ensure_profile_exists(repo, profile_id)
            row = await repo.upsert_item(
                profile_id=profile_id,
                memory_key=normalized_key,
                content=normalized_content,
                summary=normalized_summary or self._derive_summary(normalized_content),
                details_md=normalized_details,
                source=normalized_source,
                source_kind=source_kind,
                memory_kind=memory_kind,
                priority=normalized_priority,
                confidence=normalized_confidence,
                last_confirmed_at=confirmed_at,
                stale=stale,
            )
            return self._to_metadata(row)

        return await self._with_repo(_op)

    async def remember(
        self,
        *,
        profile_id: str,
        memory_key: str,
        content: str | None = None,
        summary: str | None = None,
        details_md: str | None = None,
        source: str | None = None,
        source_kind: ProfileMemorySourceKind = "manual",
        memory_kind: MemoryKind = "fact",
        priority: int = 50,
        confidence: float = 0.8,
    ) -> ProfileMemoryItemMetadata | None:
        """Safely remember only durable, non-secret profile facts."""

        normalized_content = self._normalize_content(
            content=content,
            summary=self._normalize_optional_text(summary),
            details_md=self._normalize_optional_text(details_md),
        )
        if self._looks_secret(normalized_content):
            return None
        if self._looks_volatile(normalized_content):
            return None
        return await self.upsert(
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
        )

    async def get(self, *, profile_id: str, memory_key: str) -> ProfileMemoryItemMetadata:
        """Return one durable profile-memory item by key."""

        normalized_key = self._normalize_memory_key(memory_key)

        async def _op(repo: ProfileMemoryRepository) -> ProfileMemoryItemMetadata:
            await self._ensure_profile_exists(repo, profile_id)
            row = await repo.get_by_key(profile_id=profile_id, memory_key=normalized_key)
            if row is None:
                raise ProfileMemoryServiceError(
                    error_code="profile_memory_not_found",
                    reason="Profile memory item not found",
                )
            return self._to_metadata(row)

        return await self._with_repo(_op)

    async def list(
        self,
        *,
        profile_id: str,
        include_stale: bool = False,
        limit: int = 50,
    ) -> list[ProfileMemoryItemMetadata]:
        """List durable profile-memory items ordered by usefulness."""

        normalized_limit = self._normalize_limit(limit, ceiling=500)

        async def _op(repo: ProfileMemoryRepository) -> list[ProfileMemoryItemMetadata]:
            await self._ensure_profile_exists(repo, profile_id)
            rows = await repo.list_by_profile(
                profile_id=profile_id,
                include_stale=include_stale,
                limit=normalized_limit,
            )
            return [self._to_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def mark_stale(
        self,
        *,
        profile_id: str,
        memory_key: str,
        stale: bool,
    ) -> ProfileMemoryItemMetadata:
        """Mark one durable fact stale or active."""

        normalized_key = self._normalize_memory_key(memory_key)

        async def _op(repo: ProfileMemoryRepository) -> ProfileMemoryItemMetadata:
            await self._ensure_profile_exists(repo, profile_id)
            row = await repo.mark_stale(profile_id=profile_id, memory_key=normalized_key, stale=stale)
            if row is None:
                raise ProfileMemoryServiceError(
                    error_code="profile_memory_not_found",
                    reason="Profile memory item not found",
                )
            return self._to_metadata(row)

        return await self._with_repo(_op)

    async def render_prompt_block(
        self,
        *,
        profile_id: str,
        max_chars: int = 500,
        max_items: int = 100,
        min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
    ) -> ProfileMemoryPromptBlock:
        """Render deterministic trusted prompt block from pinned core memory."""

        normalized_max_chars = self._normalize_limit(max_chars, ceiling=8000)
        normalized_max_items = self._normalize_limit(max_items, ceiling=500)
        normalized_min_confidence = self._normalize_confidence(min_confidence)
        rows = await self.list(profile_id=profile_id, include_stale=False, limit=normalized_max_items)
        lines: list[tuple[str, str]] = []
        for row in rows:
            if row.stale or row.confidence < normalized_min_confidence:
                continue
            value = row.summary or row.content
            if not value:
                continue
            lines.append((row.memory_key, f"- {row.memory_key}: {value}"))
        if not lines:
            return ProfileMemoryPromptBlock(content="", included_keys=[], truncated=False)

        header = "# Core Memory (trusted)"
        content = header
        included_keys: list[str] = []
        truncated = False
        for key, line in lines:
            candidate = f"{content}\n{line}"
            if len(candidate) > normalized_max_chars:
                truncated = True
                break
            content = candidate
            included_keys.append(key)
        if not included_keys:
            first_key, first_line = lines[0]
            content = f"{header}\n{first_line[: max(0, normalized_max_chars - len(header) - 1)]}".rstrip()
            included_keys = [first_key] if len(content) > len(header) else []
            truncated = True
        return ProfileMemoryPromptBlock(
            content=content,
            included_keys=included_keys,
            truncated=truncated,
        )

    async def shutdown(self) -> None:
        """Dispose owned async engine when service created it."""

        if self._engine is None:
            return
        await self._engine.dispose()

    async def _with_repo(
        self,
        op: Callable[[ProfileMemoryRepository], Awaitable[TValue]],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            repo = ProfileMemoryRepository(session)
            return await op(repo)

    async def _ensure_profile_exists(self, repo: ProfileMemoryRepository, profile_id: str) -> None:
        if await repo.validate_profile_exists(profile_id):
            return
        raise ProfileMemoryServiceError(error_code="profile_not_found", reason="Profile not found")

    @staticmethod
    def _normalize_memory_key(memory_key: str) -> str:
        value = memory_key.strip()
        if not value:
            raise ProfileMemoryServiceError(
                error_code="profile_memory_invalid_key",
                reason="Profile memory key is required",
            )
        if len(value) > 128:
            raise ProfileMemoryServiceError(
                error_code="profile_memory_invalid_key",
                reason="Profile memory key is too long",
            )
        return value

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @classmethod
    def _normalize_content(
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
        raise ProfileMemoryServiceError(
            error_code="profile_memory_invalid_content",
            reason="Profile memory content is required",
        )

    @staticmethod
    def _normalize_priority(priority: int) -> int:
        if priority < 0 or priority > 100:
            raise ProfileMemoryServiceError(
                error_code="profile_memory_invalid_priority",
                reason="Profile memory priority must be between 0 and 100",
            )
        return priority

    @staticmethod
    def _normalize_confidence(confidence: float) -> float:
        if confidence < 0.0 or confidence > 1.0:
            raise ProfileMemoryServiceError(
                error_code="profile_memory_invalid_confidence",
                reason="Profile memory confidence must be between 0 and 1",
            )
        return float(confidence)

    @staticmethod
    def _normalize_limit(limit: int, *, ceiling: int) -> int:
        if limit < 1:
            raise ProfileMemoryServiceError(
                error_code="profile_memory_invalid_limit",
                reason="limit must be >= 1",
            )
        return min(limit, ceiling)

    @staticmethod
    def _derive_summary(content: str) -> str:
        single_line = " ".join(content.split())
        if len(single_line) <= 180:
            return single_line
        return single_line[:177].rstrip() + "..."

    @staticmethod
    def _looks_secret(content: str) -> bool:
        return _SECRET_PATTERN.search(content) is not None

    @staticmethod
    def _looks_volatile(content: str) -> bool:
        return _VOLATILE_PATTERN.search(content) is not None

    @staticmethod
    def _to_metadata(row: ProfileMemoryItem) -> ProfileMemoryItemMetadata:
        return ProfileMemoryItemMetadata(
            id=row.id,
            profile_id=row.profile_id,
            memory_key=row.memory_key,
            source=row.source,
            source_kind=row.source_kind,
            memory_kind=row.memory_kind,
            priority=row.priority,
            confidence=row.confidence,
            stale=row.stale,
            summary=row.summary,
            details_md=row.details_md,
            content=row.content,
            last_confirmed_at=row.last_confirmed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def get_profile_memory_service(settings: Settings) -> ProfileMemoryService:
    """Get or create one profile-memory service for current root directory."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is not None:
        return service
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    service = ProfileMemoryService(factory, engine=engine)
    _SERVICES_BY_ROOT[key] = service
    return service


def reset_profile_memory_services() -> None:
    """Reset cached profile-memory services for tests."""

    _SERVICES_BY_ROOT.clear()


async def reset_profile_memory_services_async() -> None:
    """Reset cached profile-memory services and dispose owned async engines."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()
