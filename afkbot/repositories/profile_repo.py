"""Repository for profile entities."""

from __future__ import annotations

from sqlalchemy import desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.profile import Profile


class ProfileRepository:
    """Persistence operations for Profile model."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, profile_id: str) -> Profile | None:
        """Get profile by id."""

        return await self._session.get(Profile, profile_id)

    async def get_or_create_default(self, profile_id: str = "default") -> Profile:
        """Ensure a default profile exists."""

        existing = await self.get(profile_id)
        if existing is not None:
            return existing
        profile = Profile(
            id=profile_id,
            name="Default" if profile_id == "default" else profile_id,
            is_default=profile_id == "default",
            status="active",
        )
        self._session.add(profile)
        try:
            await self._session.flush()
            return profile
        except IntegrityError:
            await self._session.rollback()
            row = await self.get(profile_id)
            if row is None:
                raise
            return row

    async def create(
        self,
        *,
        profile_id: str,
        name: str,
        is_default: bool = False,
        status: str = "active",
    ) -> Profile:
        """Create one profile row and fail when id already exists."""

        profile = Profile(
            id=profile_id,
            name=name,
            is_default=is_default,
            status=status,
        )
        self._session.add(profile)
        try:
            await self._session.flush()
            return profile
        except IntegrityError as exc:
            await self._session.rollback()
            raise ValueError(f"Profile already exists: {profile_id}") from exc

    async def list_all(self) -> list[Profile]:
        """Return all profiles."""

        result = await self._session.execute(
            select(Profile).order_by(desc(Profile.is_default), Profile.id.asc())
        )
        return list(result.scalars().all())
