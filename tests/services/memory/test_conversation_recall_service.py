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
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'conversation_recall_service.db'}",
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        chat_sessions = ChatSessionRepository(session)
        await chat_sessions.create("chat:100", "default", title="Allowed Session")
        await chat_sessions.create("chat:200", "default", title="Foreign Session")
        await chat_sessions.create("profile:default:chat:100", "default", title="Routed Allowed Session")
        await chat_sessions.create("profile:default:chat:200", "default", title="Routed Foreign Session")
        await chat_sessions.create("taskflow:task-1", "default", title="Task Session")
        await chat_sessions.create("taskflow:task-2", "default", title="Task Session 2")
        await chat_sessions.create("automation-webhook-11-inbound", "default", title="Automation Session")
        await chat_sessions.create("automation-webhook-11-foreign", "default", title="Automation Session 2")
        await chat_sessions.create("automation-cron-11-nightly", "default", title="Automation Cron Session")
        await chat_sessions.create("automation-cron-22-foreign", "default", title="Automation Cron Session 2")
        session.add(
            ChatTurn(
                id=1,
                profile_id="default",
                session_id="chat:100",
                user_message="How do we keep Redis cache warm?",
                assistant_message="Use async workers and monitor eviction metrics.",
            )
        )
        session.add(
            ChatTurn(
                id=2,
                profile_id="default",
                session_id="chat:100",
                user_message="What stores source of truth?",
                assistant_message="Postgres is source of truth.",
            )
        )
        session.add(
            ChatTurn(
                id=3,
                profile_id="default",
                session_id="chat:200",
                user_message="Foreign invoice context",
                assistant_message="Invoice approval pending.",
            )
        )
        session.add(
            ChatTurn(
                id=4,
                profile_id="default",
                session_id="taskflow:task-1",
                user_message="Taskflow invoice context",
                assistant_message="Taskflow invoice approved.",
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
                session_id="automation-cron-11-nightly",
                user_message="Automation cron invoice",
                assistant_message="Automation cron invoice evidence recorded.",
            )
        )
        session.add(
            ChatTurn(
                id=7,
                profile_id="default",
                session_id="profile:default:chat:100",
                user_message="Routed chat says Postgres is source of truth.",
                assistant_message="Routed session confirms source of truth is Postgres.",
            )
        )
        session.add(
            ChatTurn(
                id=8,
                profile_id="default",
                session_id="profile:default:chat:200",
                user_message="Foreign routed invoice context.",
                assistant_message="Foreign routed invoice approval pending.",
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
        await chat_sessions.create(
            session_id="chat:100:thread:topic-1",
            profile_id="default",
            title="chat-100-thread-topic-1",
        )
        await chat_sessions.create(
            session_id="chat:100:user:user-1",
            profile_id="default",
            title="chat-100-user-user-1",
        )
        await chat_sessions.create(
            session_id="chat:100:thread:topic-1:user:user-1",
            profile_id="default",
            title="chat-100-thread-topic-1-user-user-1",
        )
        await chat_sessions.create(
            session_id="chat:100:thread:topic-9",
            profile_id="default",
            title="chat-100-thread-topic-9",
        )
        session.add(
            ChatTurn(
                id=9,
                profile_id="default",
                session_id="chat:100:thread:topic-1",
                user_message="Thread topic 1 invoice context.",
                assistant_message="Thread topic 1 invoice approval pending.",
            )
        )
        session.add(
            ChatTurn(
                id=10,
                profile_id="default",
                session_id="chat:100:user:user-1",
                user_message="User 1 invoice context.",
                assistant_message="User 1 invoice approval pending.",
            )
        )
        session.add(
            ChatTurn(
                id=11,
                profile_id="default",
                session_id="chat:100:thread:topic-1:user:user-1",
                user_message="Thread topic 1 user 1 invoice context.",
                assistant_message="Thread topic 1 user 1 invoice approval pending.",
            )
        )
        session.add(
            ChatTurn(
                id=12,
                profile_id="default",
                session_id="chat:100:thread:topic-9",
                user_message="Thread topic 9 invoice context.",
                assistant_message="Thread topic 9 invoice approval pending.",
            )
        )
        await ChatSessionCompactionRepository(session).upsert(
            profile_id="default",
            session_id="chat:100:thread:topic-1",
            summary_text="Invoice thread topic 1 summary",
            compacted_until_turn_id=1,
            source_turn_count=1,
            strategy="deterministic_v1",
        )
        await ChatSessionCompactionRepository(session).upsert(
            profile_id="default",
            session_id="chat:100:user:user-1",
            summary_text="Invoice user 1 summary",
            compacted_until_turn_id=1,
            source_turn_count=1,
            strategy="deterministic_v1",
        )
        await ChatSessionCompactionRepository(session).upsert(
            profile_id="default",
            session_id="chat:100:thread:topic-1:user:user-1",
            summary_text="Invoice topic 1 user 1 summary",
            compacted_until_turn_id=1,
            source_turn_count=1,
            strategy="deterministic_v1",
        )
        await ChatSessionCompactionRepository(session).upsert(
            profile_id="default",
            session_id="chat:100:thread:topic-9",
            summary_text="Invoice thread topic 9 summary",
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
                actor_transport="telegram",
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
            actor_session_id="cli:operator",
            actor_transport="cli",
            target_session_id="chat:200",
            query="invoice",
            limit=5,
        )
        assert items
        assert items[0].kind == "compaction"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_allows_foreign_session_for_other_trusted_transports(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        items = await service.search_for_actor(
            profile_id="default",
            actor_session_id="taskflow:task-1",
            actor_transport="taskflow",
            target_session_id="taskflow:task-1",
            query="invoice",
            limit=5,
        )
        assert items
        assert items[0].kind == "turn"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_allows_routed_target_for_user_facing_transport(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        items = await service.search_for_actor(
            profile_id="default",
            actor_session_id="profile:default:chat:100",
            actor_transport="telegram",
            target_session_id="profile:default:chat:100",
            query="source of truth",
            limit=5,
            actor_account_id="acc-1",
            actor_peer_id="100",
        )
        assert items
        assert items[0].kind == "turn"
        assert items[0].session_id == "profile:default:chat:100"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_allows_user_facing_routed_thread_session_for_same_actor(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        items = await service.search_for_actor(
            profile_id="default",
            actor_session_id="chat:100:thread:topic-1",
            actor_transport="telegram",
            target_session_id="chat:100:thread:topic-1",
            query="invoice",
            limit=5,
            actor_account_id="acc-1",
            actor_peer_id="100",
            actor_thread_id="topic-1",
        )
        assert items
        assert items[0].session_id == "chat:100:thread:topic-1"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_allows_user_facing_routed_user_session_for_same_actor(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        items = await service.search_for_actor(
            profile_id="default",
            actor_session_id="chat:100:user:user-1",
            actor_transport="telegram",
            target_session_id="chat:100:user:user-1",
            query="invoice",
            limit=5,
            actor_account_id="acc-1",
            actor_peer_id="100",
            actor_user_id="user-1",
        )
        assert items
        assert items[0].session_id == "chat:100:user:user-1"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_allows_user_facing_routed_thread_user_session_for_same_actor(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        items = await service.search_for_actor(
            profile_id="default",
            actor_session_id="chat:100:thread:topic-1:user:user-1",
            actor_transport="telegram",
            target_session_id="chat:100:thread:topic-1:user:user-1",
            query="invoice",
            limit=5,
            actor_account_id="acc-1",
            actor_peer_id="100",
            actor_thread_id="topic-1",
            actor_user_id="user-1",
        )
        assert items
        assert items[0].session_id == "chat:100:thread:topic-1:user:user-1"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_blocks_user_facing_routed_thread_session_for_other_thread(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        with pytest.raises(ConversationRecallServiceError) as exc_info:
            await service.search_for_actor(
                profile_id="default",
                actor_session_id="chat:100:thread:topic-1",
                actor_transport="telegram",
                target_session_id="chat:100:thread:topic-9",
                query="invoice",
                limit=5,
                actor_account_id="acc-1",
                actor_peer_id="100",
                actor_thread_id="topic-1",
            )
        assert exc_info.value.error_code == "memory_cross_scope_forbidden"
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
                actor_session_id="taskflow:task-1",
                actor_transport="taskflow",
                target_session_id="taskflow:task-2",
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
                actor_session_id="automation-webhook-11-inbound",
                actor_transport="automation",
                target_session_id="automation-webhook-22-foreign",
                query="invoice",
                limit=5,
            )
        assert exc_info.value.error_code == "memory_cross_scope_forbidden"
    finally:
        await engine.dispose()


async def test_conversation_recall_service_blocks_foreign_session_for_user_facing_transport_other_peer(
    tmp_path: Path,
) -> None:
    engine, service = await _prepare(tmp_path)
    try:
        with pytest.raises(ConversationRecallServiceError) as exc_info:
            await service.search_for_actor(
                profile_id="default",
                actor_session_id="profile:default:chat:100",
                actor_transport="telegram",
                target_session_id="profile:default:chat:200",
                query="invoice",
                limit=5,
                actor_account_id="acc-1",
                actor_peer_id="100",
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
