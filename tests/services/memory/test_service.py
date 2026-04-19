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
from afkbot.services.agent_loop.memory_extraction import ExtractedMemoryCandidate
from afkbot.services.memory import MemoryScopeDescriptor, MemoryService, MemoryServiceError
from afkbot.services.memory.consolidation import MemoryConsolidationService
from afkbot.services.memory.profile_memory_service import get_profile_memory_service
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


async def _prepare_with_settings(
    tmp_path: Path,
) -> tuple[Settings, AsyncEngine, MemoryService]:
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'memory_service_profile.db'}",
        root_dir=tmp_path,
        memory_core_enabled=True,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")
        await profiles.get_or_create_default("other")
    return settings, engine, MemoryService(factory, settings=settings)


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


async def test_memory_service_promote_mirrors_stable_fact_into_profile_memory(
    tmp_path: Path,
) -> None:
    """Promoting stable long-lived facts should also materialize the new pinned profile-memory tier."""

    settings, engine, service = await _prepare_with_settings(tmp_path)
    try:
        scope = _chat_scope(peer_id="100")
        await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="preferred_language",
            summary="Reply in Russian by default",
            source_kind="auto",
            memory_kind="preference",
        )

        await service.promote(
            profile_id="default",
            memory_key="preferred_language",
            from_scope=scope,
        )

        profile_memory = get_profile_memory_service(settings)
        pinned_items = await profile_memory.list(profile_id="default")
        assert any(item.memory_key == "preferred_language" for item in pinned_items)
    finally:
        await engine.dispose()


async def test_memory_service_promote_does_not_mirror_secrets_or_chat_only_facts_into_core_memory(
    tmp_path: Path,
) -> None:
    """Promoted archival rows should still pass safe core-memory gates."""

    settings, engine, service = await _prepare_with_settings(tmp_path)
    try:
        scope = _chat_scope(peer_id="100")
        await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="api_key",
            summary="API key is sk-secretsecretsecret",
            source_kind="auto",
            memory_kind="fact",
        )
        await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="chat_style",
            summary="For this chat only, answer in English.",
            source_kind="auto",
            memory_kind="preference",
        )

        await service.promote(profile_id="default", memory_key="api_key", from_scope=scope)
        await service.promote(profile_id="default", memory_key="chat_style", from_scope=scope)

        pinned_items = await get_profile_memory_service(settings).list(profile_id="default")
        pinned_keys = {item.memory_key for item in pinned_items}
        assert "api_key" not in pinned_keys
        assert "chat_style" not in pinned_keys
    finally:
        await engine.dispose()


async def test_memory_service_search_prefers_exact_logical_key_before_semantic_fallback(
    tmp_path: Path,
) -> None:
    """Search should hit an exact logical key before weaker semantic matches."""

    engine, service = await _prepare(tmp_path)
    try:
        scope = _chat_scope(peer_id="100")
        await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="preferred_language",
            summary="Reply in Russian by default",
            source_kind="manual",
            memory_kind="preference",
        )
        await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="documentation_note",
            summary="The token preferred_language appears in setup docs.",
            source_kind="manual",
            memory_kind="note",
        )

        search = await service.search(
            profile_id="default",
            scope=scope,
            query="preferred_language",
            limit=5,
        )

        assert search
        assert search[0].memory_key == "preferred_language"
    finally:
        await engine.dispose()


async def test_memory_service_search_includes_promoted_global_as_local_first_fallback(
    tmp_path: Path,
) -> None:
    """Global fallback should be owned by the service and appended only after local hits."""

    engine, service = await _prepare(tmp_path)
    try:
        scope = _chat_scope(peer_id="100")
        await service.upsert(
            profile_id="default",
            scope=scope,
            memory_key="staging_flow",
            summary="Deployment flow for this chat uses staging before production.",
            source_kind="manual",
            memory_kind="decision",
        )
        await service.upsert(
            profile_id="default",
            scope=MemoryScopeDescriptor.profile_scope(),
            memory_key="deploy_policy",
            summary="Globally, deploy directly to production.",
            source_kind="manual",
            memory_kind="decision",
            visibility="promoted_global",
        )

        search = await service.search(
            profile_id="default",
            scope=scope,
            query="deployment flow",
            include_global=True,
            global_limit=1,
            limit=2,
        )

        assert [item.memory_key for item in search] == ["staging_flow", "deploy_policy"]
        assert [item.scope_kind for item in search] == ["chat", "profile"]
    finally:
        await engine.dispose()


def test_memory_consolidation_service_plans_global_preference_for_core_and_promotion() -> None:
    """Consolidation policy should decide core/global writes outside the extractor."""

    candidate = ExtractedMemoryCandidate(
        source_text="По умолчанию отвечай по-русски и кратко",
        summary="Chat preference: По умолчанию отвечай по-русски и кратко",
        details_md="User said: По умолчанию отвечай по-русски и кратко",
        memory_kind="preference",
    )

    plan = MemoryConsolidationService.plan_candidate(candidate)

    assert plan.memory_key == "preferred_language"
    assert plan.promote_global is True
    assert plan.mirror_to_core is True
    assert plan.core_memory_key == "preferred_language"


def test_memory_consolidation_service_keeps_generic_chat_preference_local() -> None:
    """Chat-local preferences should not leak into core/global plans."""

    candidate = ExtractedMemoryCandidate(
        source_text="Отвечай по-русски и кратко",
        summary="Chat preference: Отвечай по-русски и кратко",
        details_md="User said: Отвечай по-русски и кратко",
        memory_kind="preference",
    )

    plan = MemoryConsolidationService.plan_candidate(candidate)

    assert plan.promote_global is False
    assert plan.mirror_to_core is False
    assert plan.core_memory_key is None


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
