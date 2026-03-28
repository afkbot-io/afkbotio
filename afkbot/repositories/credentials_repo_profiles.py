"""Credential profile persistence mixin for credentials repository."""

from __future__ import annotations

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.credential_profile import CredentialProfile
from afkbot.models.tool_credential_binding import ToolCredentialBinding
from afkbot.repositories.credentials_repo_common import clear_other_default_profiles


class CredentialsRepositoryProfilesMixin:
    """Profile lifecycle operations for encrypted credentials vault."""

    _session: AsyncSession

    async def create_profile(
        self,
        *,
        profile_id: str,
        integration_name: str,
        profile_key: str,
        display_name: str,
        is_default: bool = False,
    ) -> CredentialProfile:
        """Create or reactivate one credential profile."""

        existing = await self.get_profile(
            profile_id=profile_id,
            integration_name=integration_name,
            profile_key=profile_key,
            include_inactive=True,
        )
        if existing is not None:
            existing.display_name = display_name
            existing.is_active = True
            if is_default:
                existing.is_default = True
                await clear_other_default_profiles(
                    session=self._session,
                    profile_id=profile_id,
                    integration_name=integration_name,
                    except_profile_key=profile_key,
                )
            await self._session.flush()
            await self._session.refresh(existing)
            return existing

        row = CredentialProfile(
            profile_id=profile_id,
            integration_name=integration_name,
            profile_key=profile_key,
            display_name=display_name,
            is_default=is_default,
            is_active=True,
        )
        self._session.add(row)
        await self._session.flush()
        if is_default:
            await clear_other_default_profiles(
                session=self._session,
                profile_id=profile_id,
                integration_name=integration_name,
                except_profile_key=profile_key,
            )
        await self._session.refresh(row)
        return row

    async def list_profiles(
        self,
        *,
        profile_id: str,
        integration_name: str | None,
        include_inactive: bool = False,
    ) -> list[CredentialProfile]:
        """List credential profiles for profile and optional integration."""

        statement: Select[tuple[CredentialProfile]] = select(CredentialProfile).where(
            CredentialProfile.profile_id == profile_id
        )
        if integration_name is not None:
            statement = statement.where(CredentialProfile.integration_name == integration_name)
        if not include_inactive:
            statement = statement.where(CredentialProfile.is_active.is_(True))
        statement = statement.order_by(
            CredentialProfile.integration_name.asc(),
            CredentialProfile.profile_key.asc(),
            CredentialProfile.id.asc(),
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_profile(
        self,
        *,
        profile_id: str,
        integration_name: str,
        profile_key: str,
        include_inactive: bool = False,
    ) -> CredentialProfile | None:
        """Get one credential profile row by identity."""

        statement: Select[tuple[CredentialProfile]] = select(CredentialProfile).where(
            CredentialProfile.profile_id == profile_id,
            CredentialProfile.integration_name == integration_name,
            CredentialProfile.profile_key == profile_key,
        )
        if not include_inactive:
            statement = statement.where(CredentialProfile.is_active.is_(True))
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_default_profile(
        self,
        *,
        profile_id: str,
        integration_name: str,
    ) -> CredentialProfile | None:
        """Return default credential profile for integration, if any."""

        statement: Select[tuple[CredentialProfile]] = select(CredentialProfile).where(
            CredentialProfile.profile_id == profile_id,
            CredentialProfile.integration_name == integration_name,
            CredentialProfile.is_active.is_(True),
            CredentialProfile.is_default.is_(True),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def delete_profile(
        self,
        *,
        profile_id: str,
        integration_name: str,
        profile_key: str,
    ) -> bool:
        """Soft-delete one credential profile."""

        row = await self.get_profile(
            profile_id=profile_id,
            integration_name=integration_name,
            profile_key=profile_key,
        )
        if row is None:
            return False
        row.is_active = False
        row.is_default = False
        await self._session.execute(
            update(ToolCredentialBinding)
            .where(
                ToolCredentialBinding.profile_id == profile_id,
                ToolCredentialBinding.integration_name == integration_name,
                ToolCredentialBinding.credential_profile_key == profile_key,
                ToolCredentialBinding.is_active.is_(True),
            )
            .values(is_active=False)
            .execution_options(synchronize_session=False)
        )
        await self._session.flush()
        return True

    async def count_bindings_for_credential(
        self,
        *,
        profile_id: str,
        integration_name: str,
        credential_name: str,
    ) -> int:
        """Count active bindings for one credential across profile keys."""

        statement: Select[tuple[int]] = select(func.count(ToolCredentialBinding.id)).where(
            ToolCredentialBinding.profile_id == profile_id,
            ToolCredentialBinding.integration_name == integration_name,
            ToolCredentialBinding.credential_name == credential_name,
            ToolCredentialBinding.is_active.is_(True),
        )
        return int((await self._session.execute(statement)).scalar_one())
