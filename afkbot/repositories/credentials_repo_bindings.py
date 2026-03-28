"""Credential binding persistence mixin for credentials repository."""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.models.secret import Secret
from afkbot.models.tool_credential_binding import ToolCredentialBinding
from afkbot.repositories.credentials_repo_common import (
    match_binding_identity,
    normalize_tool_name,
)


class CredentialsRepositoryBindingsMixin:
    """Binding and secret row operations for encrypted credentials vault."""

    _session: AsyncSession

    async def create_binding(
        self,
        *,
        profile_id: str,
        integration_name: str,
        credential_profile_key: str,
        tool_name: str | None,
        credential_name: str,
        encrypted_value: str,
        key_version: str,
        replace_existing: bool = False,
    ) -> ToolCredentialBinding:
        """Create or replace one binding and its underlying secret row."""

        normalized_tool_name = normalize_tool_name(tool_name)
        existing = await self._get_binding_any_state(
            profile_id=profile_id,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            tool_name=normalized_tool_name,
            credential_name=credential_name,
        )
        if existing is not None and existing.is_active and not replace_existing:
            raise ValueError("credentials_conflict")

        secret = Secret(encrypted_value=encrypted_value, key_version=key_version)
        self._session.add(secret)
        await self._session.flush()

        if existing is None:
            binding = ToolCredentialBinding(
                profile_id=profile_id,
                integration_name=integration_name,
                credential_profile_key=credential_profile_key,
                tool_name=normalized_tool_name,
                credential_name=credential_name,
                secret_id=secret.id,
                is_active=True,
            )
            self._session.add(binding)
            await self._session.flush()
            await self._session.refresh(binding)
            return binding

        existing.secret_id = secret.id
        existing.is_active = True
        await self._session.flush()
        await self._session.refresh(existing)
        return existing

    async def update_binding(
        self,
        *,
        profile_id: str,
        integration_name: str,
        credential_profile_key: str,
        tool_name: str | None,
        credential_name: str,
        encrypted_value: str,
        key_version: str,
    ) -> ToolCredentialBinding | None:
        """Update active binding secret row; return None when missing."""

        normalized_tool_name = normalize_tool_name(tool_name)
        binding = await self.get_active_binding(
            profile_id=profile_id,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            tool_name=normalized_tool_name,
            credential_name=credential_name,
        )
        if binding is None:
            return None

        secret = Secret(encrypted_value=encrypted_value, key_version=key_version)
        self._session.add(secret)
        await self._session.flush()

        binding.secret_id = secret.id
        await self._session.flush()
        await self._session.refresh(binding)
        return binding

    async def delete_binding(
        self,
        *,
        profile_id: str,
        integration_name: str,
        credential_profile_key: str,
        tool_name: str | None,
        credential_name: str,
    ) -> bool:
        """Soft-delete active binding by marking it inactive."""

        normalized_tool_name = normalize_tool_name(tool_name)
        binding = await self.get_active_binding(
            profile_id=profile_id,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            tool_name=normalized_tool_name,
            credential_name=credential_name,
        )
        if binding is None:
            return False
        binding.is_active = False
        await self._session.flush()
        return True

    async def get_active_binding(
        self,
        *,
        profile_id: str,
        integration_name: str,
        credential_profile_key: str,
        tool_name: str | None,
        credential_name: str,
    ) -> ToolCredentialBinding | None:
        """Return active binding row for profile/integration/profile-key tuple."""

        normalized_tool_name = normalize_tool_name(tool_name)
        statement: Select[tuple[ToolCredentialBinding]] = select(ToolCredentialBinding).where(
            match_binding_identity(
                profile_id=profile_id,
                integration_name=integration_name,
                credential_profile_key=credential_profile_key,
                tool_name=normalized_tool_name,
                credential_name=credential_name,
            ),
            ToolCredentialBinding.is_active.is_(True),
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_bindings(
        self,
        *,
        profile_id: str,
        integration_name: str | None,
        credential_profile_key: str | None,
        tool_name: str | None,
        global_only: bool = False,
        include_inactive: bool = False,
    ) -> list[ToolCredentialBinding]:
        """List bindings for profile and optional filters."""

        normalized_tool_name = normalize_tool_name(tool_name) if tool_name is not None else None
        statement: Select[tuple[ToolCredentialBinding]] = select(ToolCredentialBinding).where(
            ToolCredentialBinding.profile_id == profile_id
        )
        if integration_name is not None:
            statement = statement.where(ToolCredentialBinding.integration_name == integration_name)
        if credential_profile_key is not None:
            statement = statement.where(
                ToolCredentialBinding.credential_profile_key == credential_profile_key
            )
        if global_only:
            statement = statement.where(ToolCredentialBinding.tool_name == "")
        elif normalized_tool_name is not None:
            statement = statement.where(ToolCredentialBinding.tool_name == normalized_tool_name)
        if not include_inactive:
            statement = statement.where(ToolCredentialBinding.is_active.is_(True))
        statement = statement.order_by(ToolCredentialBinding.id.asc())
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def get_secret_plain_binding(
        self,
        *,
        profile_id: str,
        integration_name: str,
        credential_profile_key: str,
        tool_name: str | None,
        credential_name: str,
    ) -> tuple[ToolCredentialBinding, Secret] | None:
        """Return active binding row together with linked secret row."""

        normalized_tool_name = normalize_tool_name(tool_name)
        statement: Select[tuple[ToolCredentialBinding, Secret]] = (
            select(ToolCredentialBinding, Secret)
            .join(Secret, Secret.id == ToolCredentialBinding.secret_id)
            .where(
                match_binding_identity(
                    profile_id=profile_id,
                    integration_name=integration_name,
                    credential_profile_key=credential_profile_key,
                    tool_name=normalized_tool_name,
                    credential_name=credential_name,
                ),
                ToolCredentialBinding.is_active.is_(True),
            )
        )
        row = (await self._session.execute(statement)).one_or_none()
        if row is None:
            return None
        binding, secret = row
        return binding, secret

    async def _get_binding_any_state(
        self,
        *,
        profile_id: str,
        integration_name: str,
        credential_profile_key: str,
        tool_name: str | None,
        credential_name: str,
    ) -> ToolCredentialBinding | None:
        """Return binding row regardless of active flag."""

        normalized_tool_name = normalize_tool_name(tool_name)
        statement: Select[tuple[ToolCredentialBinding]] = select(ToolCredentialBinding).where(
            match_binding_identity(
                profile_id=profile_id,
                integration_name=integration_name,
                credential_profile_key=credential_profile_key,
                tool_name=normalized_tool_name,
                credential_name=credential_name,
            )
        )
        return (await self._session.execute(statement)).scalar_one_or_none()
