"""Tests for MemoryRepository persistence and scoped semantic search."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.models.memory_item import MemoryItem
from afkbot.repositories.memory_repo import MemoryRepository
from afkbot.services.memory.contracts import MemoryScopeDescriptor
from tests.repositories._harness import build_repository_factory


def _chat_scope_key(peer_id: str) -> str:
    return MemoryScopeDescriptor(
        scope_kind="chat",
        transport="telegram_user",
        account_id="personal-user",
        peer_id=peer_id,
    ).scope_key()


def _storage_key(peer_id: str, logical_key: str) -> str:
    scope = MemoryScopeDescriptor(
        scope_kind="chat",
        transport="telegram_user",
        account_id="personal-user",
        peer_id=peer_id,
    )
    return scope.storage_key(logical_key)


async def _prepare(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    return await build_repository_factory(
        tmp_path,
        db_name="memory_repo.db",
        profile_ids=("default",),
    )


async def test_memory_repo_upsert_search_delete(tmp_path: Path) -> None:
    """Repository should support upsert, scoped search fallback, and delete."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = MemoryRepository(session)
            emb_color = [0.0] * 64
            emb_food = [0.0] * 64
            emb_query = [0.0] * 64
            emb_color[0] = 1.0
            emb_color[1] = 0.1
            emb_food[1] = 1.0
            emb_query[0] = 0.95
            emb_query[1] = 0.05

            first = await repo.upsert_item(
                profile_id="default",
                storage_key=_storage_key("100", "favorite_color"),
                logical_key="favorite_color",
                content="user likes blue color",
                summary="User likes blue",
                details_md=None,
                source="chat",
                scope_key=_chat_scope_key("100"),
                scope_kind="chat",
                transport="telegram_user",
                account_id="personal-user",
                peer_id="100",
                thread_id=None,
                user_id=None,
                session_id="chat:100",
                binding_id="personal-user",
                source_kind="manual",
                memory_kind="preference",
                visibility="local",
                embedding=emb_color,
            )
            second = await repo.upsert_item(
                profile_id="default",
                storage_key=_storage_key("100", "favorite_food"),
                logical_key="favorite_food",
                content="user likes pizza",
                summary="User likes pizza",
                details_md=None,
                source="chat",
                scope_key=_chat_scope_key("100"),
                scope_kind="chat",
                transport="telegram_user",
                account_id="personal-user",
                peer_id="100",
                thread_id=None,
                user_id=None,
                session_id="chat:100",
                binding_id="personal-user",
                source_kind="manual",
                memory_kind="preference",
                visibility="local",
                embedding=emb_food,
            )
            updated = await repo.upsert_item(
                profile_id="default",
                storage_key=_storage_key("100", "favorite_color"),
                logical_key="favorite_color",
                content="user likes dark blue color",
                summary="User likes dark blue",
                details_md=None,
                source="chat2",
                scope_key=_chat_scope_key("100"),
                scope_kind="chat",
                transport="telegram_user",
                account_id="personal-user",
                peer_id="100",
                thread_id=None,
                user_id=None,
                session_id="chat:100",
                binding_id="personal-user",
                source_kind="manual",
                memory_kind="preference",
                visibility="local",
                embedding=emb_color,
            )
            assert updated.id == first.id
            assert updated.content == "user likes dark blue color"
            assert second.id != first.id

            found = await repo.get_by_storage_key(
                profile_id="default",
                storage_key=_storage_key("100", "favorite_color"),
            )
            assert found is not None
            assert found.id == first.id
            assert found.logical_key == "favorite_color"

            search = await repo.search_by_embedding(
                profile_id="default",
                scope_key=_chat_scope_key("100"),
                query_embedding=emb_query,
                limit=2,
            )
            assert len(search) == 2
            assert search[0][0].logical_key == "favorite_color"
            assert search[0][1] <= search[1][1]

            deleted = await repo.delete_by_storage_key(
                profile_id="default",
                storage_key=_storage_key("100", "favorite_food"),
            )
            missing_delete = await repo.delete_by_storage_key(
                profile_id="default",
                storage_key=_storage_key("100", "missing"),
            )
            assert deleted is True
            assert missing_delete is False

            listed = await repo.list_by_profile(
                profile_id="default", scope_key=_chat_scope_key("100")
            )
            assert len(listed) == 1
            assert listed[0].logical_key == "favorite_color"
    finally:
        await engine.dispose()


async def test_memory_repo_trim_and_retention(tmp_path: Path) -> None:
    """Repository should prune old rows in batches and keep only newest rows."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = MemoryRepository(session)
            embedding = [0.1] * 64
            for key in ("k1", "k2", "k3", "k4"):
                await repo.upsert_item(
                    profile_id="default",
                    storage_key=_storage_key("100", key),
                    logical_key=key,
                    content=f"content {key}",
                    summary=f"summary {key}",
                    details_md=None,
                    source="chat",
                    scope_key=_chat_scope_key("100"),
                    scope_kind="chat",
                    transport="telegram_user",
                    account_id="personal-user",
                    peer_id="100",
                    thread_id=None,
                    user_id=None,
                    session_id="chat:100",
                    binding_id="personal-user",
                    source_kind="manual",
                    memory_kind="note",
                    visibility="local",
                    embedding=embedding,
                )
            profile_ids = await repo.list_profile_ids()
            assert profile_ids == ["default"]

            old_cutoff = datetime.now(timezone.utc) - timedelta(days=10)
            await session.execute(
                update(MemoryItem)
                .where(MemoryItem.profile_id == "default", MemoryItem.logical_key.in_(("k1", "k2")))
                .values(updated_at=old_cutoff)
            )
            await session.flush()

            deleted_old_first = await repo.delete_older_than(
                profile_id="default",
                cutoff=datetime.now(timezone.utc) - timedelta(days=5),
                batch_size=1,
            )
            assert deleted_old_first == 1
            deleted_old_second = await repo.delete_older_than(
                profile_id="default",
                cutoff=datetime.now(timezone.utc) - timedelta(days=5),
                batch_size=1,
            )
            assert deleted_old_second == 1
            deleted_old_third = await repo.delete_older_than(
                profile_id="default",
                cutoff=datetime.now(timezone.utc) - timedelta(days=5),
                batch_size=1,
            )
            assert deleted_old_third == 0

            deleted_overflow_first = await repo.trim_to_limit(
                profile_id="default",
                limit=1,
                batch_size=1,
            )
            assert deleted_overflow_first == 1
            deleted_overflow_second = await repo.trim_to_limit(
                profile_id="default",
                limit=1,
                batch_size=1,
            )
            assert deleted_overflow_second == 0
            listed = await repo.list_by_profile(
                profile_id="default", scope_key=_chat_scope_key("100")
            )
            assert len(listed) == 1
            assert listed[0].logical_key in {"k3", "k4"}
    finally:
        await engine.dispose()


async def test_memory_repo_search_scans_full_profile_candidate_set(tmp_path: Path) -> None:
    """Semantic search should consider older rows outside the latest 500 inserts."""

    # Arrange
    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = MemoryRepository(session)
            target_embedding = [0.0] * 64
            target_embedding[0] = 1.0
            filler_embedding = [0.0] * 64
            filler_embedding[1] = 1.0
            query_embedding = [0.0] * 64
            query_embedding[0] = 1.0

            await repo.upsert_item(
                profile_id="default",
                storage_key=_storage_key("100", "needle"),
                logical_key="needle",
                content="very specific memory",
                summary="very specific memory",
                details_md=None,
                source="chat",
                scope_key=_chat_scope_key("100"),
                scope_kind="chat",
                transport="telegram_user",
                account_id="personal-user",
                peer_id="100",
                thread_id=None,
                user_id=None,
                session_id="chat:100",
                binding_id="personal-user",
                source_kind="manual",
                memory_kind="fact",
                visibility="local",
                embedding=target_embedding,
            )
            for index in range(520):
                await repo.upsert_item(
                    profile_id="default",
                    storage_key=_storage_key("100", f"filler-{index}"),
                    logical_key=f"filler-{index}",
                    content=f"filler {index}",
                    summary=f"filler {index}",
                    details_md=None,
                    source="chat",
                    scope_key=_chat_scope_key("100"),
                    scope_kind="chat",
                    transport="telegram_user",
                    account_id="personal-user",
                    peer_id="100",
                    thread_id=None,
                    user_id=None,
                    session_id="chat:100",
                    binding_id="personal-user",
                    source_kind="manual",
                    memory_kind="note",
                    visibility="local",
                    embedding=filler_embedding,
                )

            # Act
            search = await repo.search_by_embedding(
                profile_id="default",
                scope_key=_chat_scope_key("100"),
                query_embedding=query_embedding,
                limit=3,
            )

            # Assert
            assert search
            assert search[0][0].logical_key == "needle"
    finally:
        await engine.dispose()


async def test_memory_repo_rejects_invalid_embedding_payload_on_upsert(tmp_path: Path) -> None:
    """Repository should fail closed when a caller submits one malformed embedding."""

    # Arrange
    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = MemoryRepository(session)

            # Act / Assert
            with pytest.raises(ValueError) as exc_info:
                await repo.upsert_item(
                    profile_id="default",
                    storage_key=_storage_key("100", "invalid"),
                    logical_key="invalid",
                    content="invalid embedding memory",
                    summary="invalid embedding memory",
                    details_md=None,
                    source="chat",
                    scope_key=_chat_scope_key("100"),
                    scope_kind="chat",
                    transport="telegram_user",
                    account_id="personal-user",
                    peer_id="100",
                    thread_id=None,
                    user_id=None,
                    session_id="chat:100",
                    binding_id="personal-user",
                    source_kind="manual",
                    memory_kind="note",
                    visibility="local",
                    embedding=["oops"],
                )

            assert "embedding must be a non-empty finite numeric sequence" in str(exc_info.value)
    finally:
        await engine.dispose()


async def test_memory_repo_search_skips_malformed_embeddings(tmp_path: Path) -> None:
    """Semantic search should skip malformed stored embeddings instead of crashing."""

    # Arrange
    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = MemoryRepository(session)
            valid_embedding = [0.0] * 64
            valid_embedding[0] = 1.0
            await repo.upsert_item(
                profile_id="default",
                storage_key=_storage_key("100", "valid"),
                logical_key="valid",
                content="valid memory",
                summary="valid memory",
                details_md=None,
                source="chat",
                scope_key=_chat_scope_key("100"),
                scope_kind="chat",
                transport="telegram_user",
                account_id="personal-user",
                peer_id="100",
                thread_id=None,
                user_id=None,
                session_id="chat:100",
                binding_id="personal-user",
                source_kind="manual",
                memory_kind="fact",
                visibility="local",
                embedding=valid_embedding,
            )
            session.add(
                MemoryItem(
                    profile_id="default",
                    memory_key=_storage_key("100", "invalid-text"),
                    logical_key="invalid-text",
                    content="invalid text embedding",
                    summary="invalid text embedding",
                    details_md=None,
                    source="chat",
                    scope_key=_chat_scope_key("100"),
                    scope_kind="chat",
                    transport="telegram_user",
                    account_id="personal-user",
                    peer_id="100",
                    thread_id=None,
                    user_id=None,
                    session_id="chat:100",
                    binding_id="personal-user",
                    source_kind="manual",
                    memory_kind="note",
                    visibility="local",
                    embedding=["bad"],
                )
            )
            session.add(
                MemoryItem(
                    profile_id="default",
                    memory_key=_storage_key("100", "invalid-dim"),
                    logical_key="invalid-dim",
                    content="invalid dim embedding",
                    summary="invalid dim embedding",
                    details_md=None,
                    source="chat",
                    scope_key=_chat_scope_key("100"),
                    scope_kind="chat",
                    transport="telegram_user",
                    account_id="personal-user",
                    peer_id="100",
                    thread_id=None,
                    user_id=None,
                    session_id="chat:100",
                    binding_id="personal-user",
                    source_kind="manual",
                    memory_kind="note",
                    visibility="local",
                    embedding=[1.0, 2.0],
                )
            )
            await session.flush()

            # Act
            search = await repo.search_by_embedding(
                profile_id="default",
                scope_key=_chat_scope_key("100"),
                query_embedding=valid_embedding,
                limit=5,
            )

            # Assert
            assert [item.logical_key for item, _score in search] == ["valid"]
    finally:
        await engine.dispose()
