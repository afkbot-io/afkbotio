"""Shared repository helpers that should not live in individual domain classes."""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.profile import Profile


async def profile_exists(session: AsyncSession, *, profile_id: str) -> bool:
    """Return whether the referenced profile exists in persistent storage."""

    statement: Select[tuple[str]] = select(Profile.id).where(Profile.id == profile_id)
    value = (await session.execute(statement)).scalar_one_or_none()
    return value is not None
