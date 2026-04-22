"""Tests for session service."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.chat_session import ChatSession
from afkbot.services.agent_loop.sessions import SessionProfileMismatchError, SessionService
from afkbot.settings import Settings


async def test_session_service_get_or_create(tmp_path: Path) -> None:
    """Session service should create and then reuse session id."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions.db'}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        service = SessionService(session)
        first = await service.get_or_create("s1", "default")
        second = await service.get_or_create("s1", "default")
        assert first == "s1"
        assert second == "s1"

    await engine.dispose()


async def test_session_service_rejects_profile_mismatch(tmp_path: Path) -> None:
    """Session service must reject mixed profile ownership for same session id."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions_mismatch.db'}", root_dir=tmp_path
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        service = SessionService(session)
        await service.get_or_create("s1", "profile-a")
        with pytest.raises(SessionProfileMismatchError):
            await service.get_or_create("s1", "profile-b")

    await engine.dispose()


async def test_session_service_handles_concurrent_create(tmp_path: Path) -> None:
    """Session service should remain stable under concurrent create attempts."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions_concurrent.db'}", root_dir=tmp_path
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        service = SessionService(session)
        results = await asyncio.gather(
            service.get_or_create("s-concurrent", "default"),
            service.get_or_create("s-concurrent", "default"),
            service.get_or_create("s-concurrent", "default"),
        )
        assert tuple(results) == ("s-concurrent", "s-concurrent", "s-concurrent")

        rows = (
            (await session.execute(select(ChatSession).where(ChatSession.id == "s-concurrent")))
            .scalars()
            .all()
        )
        assert len(rows) == 1

    await engine.dispose()


async def test_session_service_preserves_initial_title_for_existing_session(tmp_path: Path) -> None:
    """The first explicit session title should persist across later attaches."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'sessions_title.db'}", root_dir=tmp_path
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        service = SessionService(session)

        first = await service.get_or_create("s-title", "default", title="incident-room")
        second = await service.get_or_create("s-title", "default", title="other-room")

        row = await session.get(ChatSession, "s-title")

        assert first == "s-title"
        assert second == "s-title"
        assert row is not None
        assert row.title == "incident-room"

    await engine.dispose()
