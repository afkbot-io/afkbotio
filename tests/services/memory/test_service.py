"""Tests for scoped memory service CRUD and semantic retrieval."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.memory_item import MemoryItem
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.memory import MemoryScopeDescriptor, MemoryService, MemoryServiceError
from afkbot.settings import Settings


def _chat_scope(*, peer_id: str) -> MemoryScopeDescriptor:
    return MemoryScopeDescriptor(
        scope_kind="chat",
        transport="telegram_user",
        account_id="personal-user",
        peer_id=peer_id,
        session_id=f"chat:{peer_id}",
        binding_id="personal-user",
    )


async def _prepare(tmp_path: Path) -> tuple[AsyncEngine, MemoryService]:
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'memory_service.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")
        await profiles.get_or_create_default("other")
    return engine, MemoryService(factory)


async def test_memory_service_upsert_search_delete_in_chat_scope(tmp_path: Path) -> None:
    """Service should support full lifecycle of scoped chat memory items."""

    engine, service = await _prepare(tmp_path)
    try:
        scope = _chat_scope(peer_id="100")
        first = await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="location",
            summary="User lives in Berlin",
            details_md="Preferred city for this chat: Berlin, Germany.",
            source="chat",
            source_kind="auto",
            memory_kind="fact",
        )
        assert first.memory_key == "location"
        assert first.scope_kind == "chat"
        assert first.visibility == "local"

        updated = await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="location",
            summary="User lives in Berlin, Germany",
            details_md="Keep answers in Berlin context for this chat.",
            source="chat2",
            source_kind="manual",
            memory_kind="preference",
        )
        assert updated.id == first.id
        assert updated.source == "chat2"
        assert updated.memory_kind == "preference"

        search = await service.search(
            profile_id="default",
            scope=scope,
            query="where does the user live",
            limit=2,
        )
        assert len(search) == 1
        assert search[0].memory_key == "location"
        assert search[0].score is not None

        deleted = await service.delete(profile_id="default", scope=scope, memory_key="location")
        assert deleted is True
        with pytest.raises(MemoryServiceError) as exc_info:
            await service.delete(profile_id="default", scope=scope, memory_key="location")
        assert exc_info.value.error_code == "memory_not_found"
    finally:
        await engine.dispose()


async def test_memory_service_chat_scope_isolation(tmp_path: Path) -> None:
    """Scoped search should not leak one chat's memory into another chat."""

    engine, service = await _prepare(tmp_path)
    try:
        scope_a = _chat_scope(peer_id="100")
        scope_b = _chat_scope(peer_id="200")
        await service.upsert(
            profile_id="default",
            scope=scope_a,
            memory_key="tone",
            summary="Use short answers in chat A",
            source_kind="auto",
            memory_kind="preference",
        )
        await service.upsert(
            profile_id="default",
            scope=scope_b,
            memory_key="tone",
            summary="Use detailed answers in chat B",
            source_kind="auto",
            memory_kind="preference",
        )

        search_a = await service.search(
            profile_id="default",
            scope=scope_a,
            query="how should replies look",
            limit=5,
        )
        search_b = await service.search(
            profile_id="default",
            scope=scope_b,
            query="how should replies look",
            limit=5,
        )
        assert len(search_a) == 1
        assert "chat A" in search_a[0].content
        assert len(search_b) == 1
        assert "chat B" in search_b[0].content
    finally:
        await engine.dispose()


async def test_memory_service_profile_global_memory_stays_explicit(tmp_path: Path) -> None:
    """Profile-global memory should be readable explicitly and promotable from chat scope."""

    engine, service = await _prepare(tmp_path)
    try:
        scope = _chat_scope(peer_id="100")
        await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="language",
            summary="In this chat prefer Russian",
            source_kind="auto",
            memory_kind="preference",
        )
        promoted = await service.promote(
            profile_id="default",
            memory_key="language",
            from_scope=scope,
            target_memory_key="preferred_language",
        )
        assert promoted.scope_kind == "profile"
        assert promoted.visibility == "promoted_global"
        global_search = await service.search(
            profile_id="default",
            scope=MemoryScopeDescriptor.profile_scope(),
            visibility="promoted_global",
            query="what language should be used",
            limit=5,
        )
        assert len(global_search) == 1
        assert global_search[0].memory_key == "preferred_language"
    finally:
        await engine.dispose()


async def test_memory_service_profile_isolation(tmp_path: Path) -> None:
    """Memory service should still enforce profile boundaries."""

    engine, service = await _prepare(tmp_path)
    try:
        await service.upsert(
            profile_id="default",
            scope=_chat_scope(peer_id="100"),
            memory_key="private",
            summary="private note",
            source_kind="manual",
        )
        other_search = await service.search(
            profile_id="other",
            scope=_chat_scope(peer_id="100"),
            query="private",
            limit=5,
        )
        assert other_search == []

        with pytest.raises(MemoryServiceError) as exc_info:
            await service.search(profile_id="missing", query="private", limit=5)
        assert exc_info.value.error_code == "profile_not_found"
    finally:
        await engine.dispose()


async def test_memory_service_retention_and_trim(tmp_path: Path) -> None:
    """Service garbage collector should enforce retention/trim in bounded batches."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'memory_gc.db'}",
        root_dir=tmp_path,
        memory_retention_days=365,
        memory_max_items_per_profile=50,
        memory_gc_batch_size=1,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    writer_service = MemoryService(
        factory,
        retention_days=365,
        max_items_per_profile=50,
        gc_batch_size=1,
    )
    gc_service = MemoryService(
        factory,
        retention_days=1,
        max_items_per_profile=1,
        gc_batch_size=1,
    )
    try:
        scope = _chat_scope(peer_id="100")
        await writer_service.upsert(
            profile_id="default", scope=scope, memory_key="k1", summary="first", source="chat"
        )
        await writer_service.upsert(
            profile_id="default", scope=scope, memory_key="k2", summary="second", source="chat"
        )
        await writer_service.upsert(
            profile_id="default", scope=scope, memory_key="k3", summary="third", source="chat"
        )
        await writer_service.upsert(
            profile_id="default", scope=scope, memory_key="k4", summary="fourth", source="chat"
        )

        async with session_scope(factory) as session:
            old_time = datetime.now(timezone.utc) - timedelta(days=3)
            await session.execute(
                update(MemoryItem)
                .where(MemoryItem.profile_id == "default", MemoryItem.logical_key.in_(("k1", "k2")))
                .values(updated_at=old_time)
            )

        deleted = await gc_service.garbage_collect(profile_id="default")
        assert deleted == 3

        left = await gc_service.search(profile_id="default", scope=scope, query="third", limit=10)
        keys = {item.memory_key for item in left}
        assert keys <= {"k3", "k4"}
        assert len(keys) == 1
    finally:
        await engine.dispose()
