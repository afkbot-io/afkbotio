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
        profile_db_url=None,
        root_dir=tmp_path,
    )
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    await create_schema(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).create(profile_id="default", name="Default")
        session_repo = ChatSessionRepository(session)
        await session_repo.create("chat:100", "default", "Primary")
        await session_repo.create("chat:200", "default", "Target")
        await session_repo.create("cli:200", "default", "CLI target")
        await session_repo.create("taskflow:task-11", "default", "Taskflow target")
        await session_repo.create("taskflow:task-22", "default", "Taskflow foreign")
        await session_repo.create("automation-webhook-11-foreign", "default", "Automation webhook")
        await session_repo.create("automation-cron-11-foreign", "default", "Automation cron")
        session.add(
            ChatTurn(
                id=1,
                profile_id="default",
                session_id="chat:200",
                user_message="Where is the invoice archive?",
                assistant_message="Invoice archive is in /srv/invoices.",
            )
        )
        session.add(
            ChatTurn(
                id=2,
                profile_id="default",
                session_id="chat:100",
                user_message="Need source of truth",
                assistant_message="Source of truth is docs/architecture.md",
            )
        )
        session.add(
            ChatTurn(
                id=3,
                profile_id="default",
                session_id="cli:200",
                user_message="Find invoice evidence",
                assistant_message="Invoice evidence is in /srv/invoices.",
            )
        )
        session.add(
            ChatTurn(
                id=4,
                profile_id="default",
                session_id="taskflow:task-11",
                user_message="Taskflow invoice lookup",
                assistant_message="Taskflow invoice evidence recorded.",
            )
        )
        session.add(
            ChatTurn(
                id=5,
                profile_id="default",
                session_id="automation-webhook-11-foreign",
                user_message="Automation webhook invoice",
                assistant_message="Automation webhook invoice evidence recorded.",
            )
        )
        session.add(
            ChatTurn(
                id=6,
                profile_id="default",
                session_id="automation-cron-11-foreign",
                user_message="Automation cron invoice",
                assistant_message="Automation cron invoice evidence recorded.",
            )
        )
        await ChatSessionCompactionRepository(session).upsert(
            profile_id="default",
            session_id="chat:200",
            summary_text="Invoice archive summary",
            compacted_until_turn_id=1,
            source_turn_count=1,
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
            actor_session_id="cli:default",
            actor_transport="cli",
            target_session_id="cli:200",
            query="invoice",
            limit=5,
        )
        assert items
        assert items[0].session_id == "cli:200"
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("transport", "actor_session_id", "target_session_id"),
    [
        ("automation", "automation-webhook-11-own", "automation-webhook-11-foreign"),
        ("automation", "automation-cron-11-own", "automation-cron-11-foreign"),
        ("taskflow", "taskflow:task-11", "taskflow:task-11"),
    ],
)
async def test_conversation_recall_service_allows_foreign_session_for_other_trusted_transports(
    tmp_path: Path,
    transport: str,
    actor_session_id: str,
    target_session_id: str,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        items = await service.search_for_actor(
            profile_id="default",
            actor_session_id=actor_session_id,
            actor_transport=transport,
            target_session_id=target_session_id,
            query="invoice",
            limit=5,
        )
        assert items
        assert items[0].session_id == target_session_id
    finally:
        await engine.dispose()


async def test_conversation_recall_service_blocks_foreign_session_for_trusted_transport_other_owner(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        with pytest.raises(ConversationRecallServiceError) as exc_info:
            await service.search_for_actor(
                profile_id="default",
                actor_session_id="taskflow:task-11",
                actor_transport="taskflow",
                target_session_id="taskflow:task-22",
                query="invoice",
                limit=5,
            )
        assert exc_info.value.error_code == "memory_cross_scope_forbidden"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_blocks_foreign_session_for_trusted_transport_outside_namespace(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        with pytest.raises(ConversationRecallServiceError) as exc_info:
            await service.search_for_actor(
                profile_id="default",
                actor_session_id="automation-webhook-11-own",
                actor_transport="automation",
                target_session_id="chat:200",
                query="invoice",
                limit=5,
            )
        assert exc_info.value.error_code == "memory_cross_scope_forbidden"
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
