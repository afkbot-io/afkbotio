"""Credential profile management methods for credentials service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from afkbot.repositories.credentials_repo import CredentialsRepository
from afkbot.services.credentials.contracts import CredentialProfileMetadata
from afkbot.services.credentials.metadata import to_profile_metadata
from afkbot.services.credentials.repository_support import ensure_profile_exists
from afkbot.services.credentials.targets import normalize_integration_name, normalize_profile_key

TRepoValue = TypeVar("TRepoValue")


class CredentialsProfileMixin:
    """Profile lifecycle operations mixed into credentials service."""

    if TYPE_CHECKING:
        async def _with_repo(
            self,
            op: Callable[[CredentialsRepository], Awaitable[TRepoValue]],
        ) -> TRepoValue: ...

    async def create_profile(
        self,
        *,
        profile_id: str,
        integration_name: str,
        profile_key: str,
        display_name: str | None = None,
        is_default: bool = False,
    ) -> CredentialProfileMetadata:
        """Create or reactivate credential profile for integration."""

        normalized_integration = normalize_integration_name(integration_name)
        normalized_profile_key = normalize_profile_key(profile_key)
        normalized_display_name = (display_name or normalized_profile_key).strip() or normalized_profile_key

        async def _op(repo: CredentialsRepository) -> CredentialProfileMetadata:
            await ensure_profile_exists(repo, profile_id=profile_id)
            row = await repo.create_profile(
                profile_id=profile_id,
                integration_name=normalized_integration,
                profile_key=normalized_profile_key,
                display_name=normalized_display_name,
                is_default=is_default,
            )
            return to_profile_metadata(row)

        return await self._with_repo(_op)

    async def list_profiles(
        self,
        *,
        profile_id: str,
        integration_name: str | None = None,
        include_inactive: bool = False,
    ) -> list[CredentialProfileMetadata]:
        """List credential profiles for profile and optional integration."""

        normalized_integration = (
            None if integration_name is None else normalize_integration_name(integration_name)
        )

        async def _op(repo: CredentialsRepository) -> list[CredentialProfileMetadata]:
            await ensure_profile_exists(repo, profile_id=profile_id)
            rows = await repo.list_profiles(
                profile_id=profile_id,
                integration_name=normalized_integration,
                include_inactive=include_inactive,
            )
            return [to_profile_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def delete_profile(
        self,
        *,
        profile_id: str,
        integration_name: str,
        profile_key: str,
    ) -> bool:
        """Deactivate one credential profile."""

        normalized_integration = normalize_integration_name(integration_name)
        normalized_profile_key = normalize_profile_key(profile_key)

        async def _op(repo: CredentialsRepository) -> bool:
            await ensure_profile_exists(repo, profile_id=profile_id)
            return await repo.delete_profile(
                profile_id=profile_id,
                integration_name=normalized_integration,
                profile_key=normalized_profile_key,
            )

        return await self._with_repo(_op)
