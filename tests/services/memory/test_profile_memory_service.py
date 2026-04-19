"""Tests for profile/core memory service foundation behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.memory.profile_memory_service import (
    ProfileMemoryService,
    ProfileMemoryServiceError,
)
from afkbot.settings import Settings


async def _prepare(tmp_path: Path) -> tuple[AsyncEngine, ProfileMemoryService]:
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'profile_memory_service.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")
    return engine, ProfileMemoryService(factory)


async def test_profile_memory_service_remember_filters_secret_and_volatile_content(
    tmp_path: Path,
) -> None:
    """The safe remember path should reject secrets and session-bound facts."""

    engine, service = await _prepare(tmp_path)
    try:
        stored = await service.remember(
            profile_id="default",
            memory_key="preferred_language",
            summary="Reply in Russian",
            details_md="Durable communication preference.",
            memory_kind="preference",
            priority=90,
            confidence=0.95,
        )
        volatile = await service.remember(
            profile_id="default",
            memory_key="temporary_tone",
            summary="For this chat only, be extra formal",
            memory_kind="preference",
        )
        secret = await service.remember(
            profile_id="default",
            memory_key="api_key",
            summary="API key is sk-secretsecretsecret",
            memory_kind="fact",
        )

        assert stored is not None
        assert volatile is None
        assert secret is None

        listed = await service.list(profile_id="default")
        assert [item.memory_key for item in listed] == ["preferred_language"]
    finally:
        await engine.dispose()


async def test_profile_memory_service_upsert_updates_existing_fact(tmp_path: Path) -> None:
    """Repeated writes to the same logical key should merge into one durable fact."""

    engine, service = await _prepare(tmp_path)
    try:
        first = await service.upsert(
            profile_id="default",
            memory_key="tone",
            summary="Keep responses concise",
            memory_kind="preference",
            priority=70,
            confidence=0.90,
        )
        updated = await service.upsert(
            profile_id="default",
            memory_key="tone",
            summary="Keep responses concise and direct",
            details_md="Confirmed across several chats.",
            source="reflection",
            source_kind="reflection",
            memory_kind="preference",
            priority=85,
            confidence=0.96,
        )

        assert updated.id == first.id
        assert updated.summary == "Keep responses concise and direct"
        assert updated.priority == 85
        assert updated.source_kind == "reflection"

        fetched = await service.get(profile_id="default", memory_key="tone")
        assert fetched.id == updated.id
        assert fetched.details_md == "Confirmed across several chats."

        with pytest.raises(ProfileMemoryServiceError) as exc_info:
            await service.get(profile_id="default", memory_key="missing")
        assert exc_info.value.error_code == "profile_memory_not_found"
    finally:
        await engine.dispose()


async def test_profile_memory_service_render_respects_budget_and_quality(
    tmp_path: Path,
) -> None:
    """Prompt rendering should stay deterministic and exclude noisy facts."""

    engine, service = await _prepare(tmp_path)
    try:
        await service.upsert(
            profile_id="default",
            memory_key="preferred_language",
            summary="Reply in Russian",
            memory_kind="preference",
            priority=95,
            confidence=0.99,
        )
        await service.upsert(
            profile_id="default",
            memory_key="tone",
            summary="Keep responses concise",
            memory_kind="preference",
            priority=80,
            confidence=0.92,
        )
        await service.upsert(
            profile_id="default",
            memory_key="timezone",
            summary="Timezone is Europe/Moscow",
            memory_kind="fact",
            priority=60,
            confidence=0.10,
        )
        await service.upsert(
            profile_id="default",
            memory_key="legacy_language",
            summary="Reply in English",
            memory_kind="preference",
            priority=99,
            confidence=0.99,
        )
        await service.mark_stale(profile_id="default", memory_key="legacy_language", stale=True)

        rendered = await service.render_prompt_block(
            profile_id="default",
            max_chars=200,
        )
        rendered_again = await service.render_prompt_block(
            profile_id="default",
            max_chars=200,
        )
        budgeted = await service.render_prompt_block(
            profile_id="default",
            max_chars=70,
        )

        assert rendered.content == rendered_again.content
        assert rendered.included_keys == ["preferred_language", "tone"]
        assert "Reply in Russian" in rendered.content
        assert "Keep responses concise" in rendered.content
        assert "Reply in English" not in rendered.content
        assert "Timezone is Europe/Moscow" not in rendered.content
        assert rendered.truncated is False

        assert budgeted.included_keys == ["preferred_language"]
        assert budgeted.truncated is True
    finally:
        await engine.dispose()
