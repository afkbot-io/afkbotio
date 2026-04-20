"""Tests for profile/core memory repository persistence behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.profile_memory_repo import ProfileMemoryRepository
from tests.repositories._harness import build_repository_factory


async def _prepare(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    return await build_repository_factory(
        tmp_path,
        db_name="profile_memory_repo.db",
        profile_ids=("default",),
    )


async def test_profile_memory_repo_upsert_list_mark_stale_and_delete(tmp_path: Path) -> None:
    """Repository should update by logical key and hide stale rows by default."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = ProfileMemoryRepository(session)
            now = datetime.now(timezone.utc)
            first = await repo.upsert_item(
                profile_id="default",
                memory_key="preferred_language",
                content="Reply in Russian",
                summary="Reply in Russian",
                details_md="Stable user preference for language.",
                source="manual",
                source_kind="manual",
                memory_kind="preference",
                priority=90,
                confidence=0.95,
                last_confirmed_at=now,
                stale=False,
            )
            second = await repo.upsert_item(
                profile_id="default",
                memory_key="tone",
                content="Keep responses concise",
                summary="Keep responses concise",
                details_md=None,
                source="manual",
                source_kind="manual",
                memory_kind="preference",
                priority=70,
                confidence=0.80,
                last_confirmed_at=now - timedelta(days=1),
                stale=False,
            )
            updated = await repo.upsert_item(
                profile_id="default",
                memory_key="preferred_language",
                content="Reply in Russian by default",
                summary="Reply in Russian by default",
                details_md="Confirmed durable preference.",
                source="auto",
                source_kind="auto",
                memory_kind="preference",
                priority=95,
                confidence=0.99,
                last_confirmed_at=now + timedelta(minutes=1),
                stale=False,
            )

            assert updated.id == first.id
            assert updated.content == "Reply in Russian by default"
            assert updated.priority == 95

            listed = await repo.list_by_profile(profile_id="default")
            assert [item.memory_key for item in listed] == [
                "preferred_language",
                "tone",
            ]
            assert listed[0].id == updated.id
            assert listed[1].id == second.id

            stale_row = await repo.mark_stale(
                profile_id="default",
                memory_key="tone",
                stale=True,
            )
            assert stale_row is not None
            assert stale_row.stale is True

            active_rows = await repo.list_by_profile(profile_id="default")
            assert [item.memory_key for item in active_rows] == ["preferred_language"]

            all_rows = await repo.list_by_profile(profile_id="default", include_stale=True)
            assert [item.memory_key for item in all_rows] == [
                "preferred_language",
                "tone",
            ]
            assert all_rows[1].stale is True

            deleted = await repo.delete_by_key(profile_id="default", memory_key="tone")
            missing_delete = await repo.delete_by_key(profile_id="default", memory_key="missing")
            assert deleted is True
            assert missing_delete is False
    finally:
        await engine.dispose()


async def test_profile_memory_repo_orders_by_priority_confidence_and_confirmation(
    tmp_path: Path,
) -> None:
    """Higher-priority, higher-confidence, fresher facts should sort first."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = ProfileMemoryRepository(session)
            base = datetime.now(timezone.utc)
            await repo.upsert_item(
                profile_id="default",
                memory_key="timezone",
                content="Timezone is Europe/Moscow",
                summary="Timezone is Europe/Moscow",
                details_md=None,
                source="manual",
                source_kind="manual",
                memory_kind="fact",
                priority=80,
                confidence=0.70,
                last_confirmed_at=base,
                stale=False,
            )
            await repo.upsert_item(
                profile_id="default",
                memory_key="preferred_language",
                content="Reply in Russian",
                summary="Reply in Russian",
                details_md=None,
                source="manual",
                source_kind="manual",
                memory_kind="preference",
                priority=100,
                confidence=0.65,
                last_confirmed_at=base + timedelta(minutes=1),
                stale=False,
            )
            await repo.upsert_item(
                profile_id="default",
                memory_key="tone",
                content="Keep responses concise",
                summary="Keep responses concise",
                details_md=None,
                source="manual",
                source_kind="manual",
                memory_kind="preference",
                priority=100,
                confidence=0.95,
                last_confirmed_at=base + timedelta(minutes=2),
                stale=False,
            )

            rows = await repo.list_by_profile(profile_id="default", include_stale=True)
            assert [item.memory_key for item in rows] == [
                "tone",
                "preferred_language",
                "timezone",
            ]
            assert await repo.validate_profile_exists("default") is True
            assert await repo.validate_profile_exists("missing") is False
    finally:
        await engine.dispose()
