"""Repository facade for credentials profiles, bindings, and secret rows."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.secret import Secret
from afkbot.repositories.credentials_repo_bindings import CredentialsRepositoryBindingsMixin
from afkbot.repositories.credentials_repo_profiles import CredentialsRepositoryProfilesMixin
from afkbot.repositories.support import profile_exists


class CredentialsRepository(
    CredentialsRepositoryBindingsMixin,
    CredentialsRepositoryProfilesMixin,
):
    """Persistence operations for encrypted credentials vault records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def validate_profile_exists(self, profile_id: str) -> bool:
        """Return True when profile exists in storage."""

        return await profile_exists(self._session, profile_id=profile_id)

    async def get_secret(self, secret_id: int) -> Secret | None:
        """Return secret row by primary key."""

        return await self._session.get(Secret, secret_id)
