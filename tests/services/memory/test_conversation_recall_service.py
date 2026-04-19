"""Tests for service-level conversation recall authorization and ranking."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.chat_turn import ChatTurn
from afkbot.repositories.chat_session_compaction_repo import ChatSessionCompactionRepository
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.memory.conversation_recall import (
    ConversationRecallService,
    ConversationRecallServiceError,
)
from afkbot.settings import Settings


async def _prepare(tmp_path: Path) -> tuple[AsyncEngine, ConversationRecallService]:
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'conversation_recall_service.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        await profiles.get_or_create_default("default")
        sessions = ChatSessionRepository(session)
        await sessions.create(session_id="chat:100", profile_id="default")
        await sessions.create(session_id="chat:200", profile_id="default")
        session.add_all(
            [
                ChatTurn(
                    session_id="chat:100",
                    profile_id="default",
                    user_message="We decided Postgres is the source of truth.",
                    assistant_message="Postgres remains the source of truth.",
                ),
                ChatTurn(
                    session_id="chat:200",
                    profile_id="default",
                    user_message="Invoice approval is still pending.",
                    assistant_message="Invoice approval is still pending.",
                ),
            ]
        )
        await session.flush()
        await ChatSessionCompactionRepository(session).upsert(
            profile_id="default",
            session_id="chat:100",
            summary_text="Earlier summary: Redis cache helps hot reads.",
            compacted_until_turn_id=0,
            source_turn_count=0,
            strategy="deterministic_v1",
        )
    return engine, ConversationRecallService(factory)


async def test_conversation_recall_service_blocks_foreign_session_without_trusted_transport(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        with pytest.raises(ConversationRecallServiceError) as exc_info:
            await service.search_for_actor(
                profile_id="default",
                actor_session_id="chat:100",
                actor_transport=None,
                target_session_id="chat:200",
                query="invoice",
                limit=5,
            )
        assert exc_info.value.error_code == "memory_cross_scope_forbidden"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_allows_foreign_session_for_cli(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        items = await service.search_for_actor(
            profile_id="default",
            actor_session_id="cli:memory",
            actor_transport="cli",
            target_session_id="chat:200",
            query="invoice",
            limit=5,
        )
        assert items
        assert items[0].session_id == "chat:200"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("transport", ["automation", "taskflow"])
async def test_conversation_recall_service_allows_foreign_session_for_other_trusted_transports(
    tmp_path: Path,
    transport: str,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        items = await service.search_for_actor(
            profile_id="default",
            actor_session_id=f"{transport}:memory",
            actor_transport=transport,
            target_session_id="chat:200",
            query="invoice",
            limit=5,
        )
        assert items
        assert items[0].session_id == "chat:200"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_blocks_foreign_session_for_unknown_transport(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        with pytest.raises(ConversationRecallServiceError) as exc_info:
            await service.search_for_actor(
                profile_id="default",
                actor_session_id="daemon:memory",
                actor_transport="daemon",
                target_session_id="chat:200",
                query="invoice",
                limit=5,
            )
        assert exc_info.value.error_code == "memory_cross_scope_forbidden"
    finally:
        await engine.dispose()
