"""Shared database/bootstrap helpers for repository tests."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.settings import Settings


async def build_repository_factory(
    tmp_path: Path,
    *,
    db_name: str,
    profile_ids: tuple[str, ...] = ("default",),
    session_specs: tuple[tuple[str, str], ...] = (),
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create a temporary repository database with optional profiles and sessions."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        profiles = ProfileRepository(session)
        for profile_id in profile_ids:
            await profiles.get_or_create_default(profile_id)
        if session_specs:
            sessions = ChatSessionRepository(session)
            for session_id, profile_id in session_specs:
                await sessions.create(session_id=session_id, profile_id=profile_id)
    return engine, factory
