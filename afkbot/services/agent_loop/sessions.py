"""Session helper service for chat loop."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from typing import ClassVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.settings import Settings


class SessionProfileMismatchError(RuntimeError):
    """Raised when a chat session belongs to another profile."""


class SessionService:
    """Service for loading or creating chat sessions."""

    _index_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _session_locks: ClassVar[MutableMapping[str, asyncio.Lock]] = {}

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = ChatSessionRepository(session)
        self._profile_repo = ProfileRepository(session)

    async def get_or_create(
        self,
        session_id: str,
        profile_id: str,
        title: str | None = None,
    ) -> str:
        """Return existing session id or create it."""

        lock = await self._get_session_lock(session_id)
        async with lock:
            existing = await self._repo.get(session_id)
            if existing is not None:
                self._ensure_profile_match(existing.profile_id, profile_id, session_id)
                return existing.id
            try:
                await self._profile_repo.get_or_create_default(profile_id)
                row = await self._repo.create(
                    session_id=session_id,
                    profile_id=profile_id,
                    title=title,
                )
                return row.id
            except IntegrityError:
                await self._session.rollback()
                existing = await self._repo.get(session_id)
                if existing is None:
                    raise
                self._ensure_profile_match(existing.profile_id, profile_id, session_id)
                return existing.id

    @classmethod
    async def _get_session_lock(cls, session_id: str) -> asyncio.Lock:
        """Get a per-session in-process lock."""

        async with cls._index_lock:
            lock = cls._session_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                cls._session_locks[session_id] = lock
            return lock

    @staticmethod
    def _ensure_profile_match(existing: str, requested: str, session_id: str) -> None:
        """Validate that session ownership is not mixed across profiles."""

        if existing != requested:
            raise SessionProfileMismatchError(
                f"session '{session_id}' belongs to profile '{existing}', requested '{requested}'"
            )


async def ensure_session_exists(
    *,
    settings: Settings,
    profile_id: str,
    session_id: str,
    title: str | None = None,
) -> None:
    """Persist one chat session row ahead of first-turn execution."""

    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            await SessionService(session).get_or_create(
                session_id=session_id,
                profile_id=profile_id,
                title=title,
            )
    finally:
        await engine.dispose()
