"""Credential binding CRUD methods for credentials service."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy.exc import IntegrityError

from afkbot.repositories.credentials_repo import CredentialsRepository
from afkbot.services.credentials.contracts import CredentialBindingMetadata
from afkbot.services.credentials.errors import CredentialsServiceError
from afkbot.services.credentials.metadata import to_binding_metadata
from afkbot.services.credentials.repository_support import (
    ensure_profile_exists,
    ensure_profile_key,
    get_secret,
    resolve_unique_binding_for_alias,
)
from afkbot.services.credentials.targets import (
    normalize_list_target,
    normalize_profile_key,
    normalize_tool_target,
    validate_credential_name,
)

TRepoValue = TypeVar("TRepoValue")


class CredentialsBindingMixin:
    """Binding CRUD operations mixed into credentials service."""

    if TYPE_CHECKING:
        async def _with_repo(
            self,
            op: Callable[[CredentialsRepository], Awaitable[TRepoValue]],
        ) -> TRepoValue: ...

        def _encrypt(self, secret_value: str) -> tuple[str, str]: ...

    async def create(
        self,
        *,
        profile_id: str,
        tool_name: str | None,
        credential_name: str,
        secret_value: str,
        replace_existing: bool = False,
        integration_name: str | None = None,
        credential_profile_key: str | None = None,
    ) -> CredentialBindingMetadata:
        """Create credential binding and return metadata without plaintext."""

        validate_credential_name(credential_name)
        target = normalize_tool_target(
            tool_name=tool_name,
            integration_name=integration_name,
        )
        normalized_profile_key = normalize_profile_key(credential_profile_key or "default")
        encrypted_value, key_version = self._encrypt(secret_value)

        async def _op(repo: CredentialsRepository) -> CredentialBindingMetadata:
            await ensure_profile_exists(repo, profile_id=profile_id)
            await ensure_profile_key(
                repo,
                profile_id=profile_id,
                integration_name=target.integration_name,
                credential_profile_key=normalized_profile_key,
            )
            try:
                binding = await repo.create_binding(
                    profile_id=profile_id,
                    integration_name=target.integration_name,
                    credential_profile_key=normalized_profile_key,
                    tool_name=target.tool_name,
                    credential_name=credential_name,
                    encrypted_value=encrypted_value,
                    key_version=key_version,
                    replace_existing=replace_existing,
                )
            except (ValueError, IntegrityError) as exc:
                raise CredentialsServiceError(
                    error_code="credentials_conflict",
                    reason="Credential binding already exists",
                ) from exc
            return to_binding_metadata(binding=binding, key_version=key_version)

        return await self._with_repo(_op)

    async def update(
        self,
        *,
        profile_id: str,
        tool_name: str | None,
        credential_name: str,
        secret_value: str,
        integration_name: str | None = None,
        credential_profile_key: str | None = None,
    ) -> CredentialBindingMetadata:
        """Update existing credential binding and return metadata."""

        validate_credential_name(credential_name)
        target = normalize_tool_target(
            tool_name=tool_name,
            integration_name=integration_name,
        )
        normalized_profile_key = normalize_profile_key(credential_profile_key or "default")
        encrypted_value, key_version = self._encrypt(secret_value)

        async def _op(repo: CredentialsRepository) -> CredentialBindingMetadata:
            await ensure_profile_exists(repo, profile_id=profile_id)
            await ensure_profile_key(
                repo,
                profile_id=profile_id,
                integration_name=target.integration_name,
                credential_profile_key=normalized_profile_key,
            )
            binding = await repo.update_binding(
                profile_id=profile_id,
                integration_name=target.integration_name,
                credential_profile_key=normalized_profile_key,
                tool_name=target.tool_name,
                credential_name=credential_name,
                encrypted_value=encrypted_value,
                key_version=key_version,
            )
            if binding is None:
                raise CredentialsServiceError(
                    error_code="credentials_not_found",
                    reason="Credential binding not found",
                )
            return to_binding_metadata(binding=binding, key_version=key_version)

        return await self._with_repo(_op)

    async def delete(
        self,
        *,
        profile_id: str,
        tool_name: str | None,
        credential_name: str,
        integration_name: str | None = None,
        credential_profile_key: str | None = None,
    ) -> bool:
        """Deactivate existing binding; return True when row was changed."""

        validate_credential_name(credential_name)
        target = normalize_tool_target(
            tool_name=tool_name,
            integration_name=integration_name,
        )
        normalized_profile_key = normalize_profile_key(credential_profile_key or "default")

        async def _op(repo: CredentialsRepository) -> bool:
            await ensure_profile_exists(repo, profile_id=profile_id)
            if target.integration_alias_mode:
                binding = await resolve_unique_binding_for_alias(
                    repo,
                    profile_id=profile_id,
                    integration_name=target.integration_name,
                    credential_profile_key=normalized_profile_key,
                    credential_name=credential_name,
                    op_name="delete",
                )
                if binding is None:
                    deleted = False
                else:
                    deleted = await repo.delete_binding(
                        profile_id=profile_id,
                        integration_name=binding.integration_name,
                        credential_profile_key=binding.credential_profile_key,
                        tool_name=binding.tool_name or None,
                        credential_name=binding.credential_name,
                    )
            else:
                deleted = await repo.delete_binding(
                    profile_id=profile_id,
                    integration_name=target.integration_name,
                    credential_profile_key=normalized_profile_key,
                    tool_name=target.tool_name,
                    credential_name=credential_name,
                )
            if not deleted:
                raise CredentialsServiceError(
                    error_code="credentials_not_found",
                    reason="Credential binding not found",
                )
            return True

        return await self._with_repo(_op)

    async def get(
        self,
        *,
        profile_id: str,
        tool_name: str | None,
        credential_name: str,
        integration_name: str | None = None,
        credential_profile_key: str | None = None,
    ) -> CredentialBindingMetadata:
        """Get one active credential binding metadata without plaintext."""

        validate_credential_name(credential_name)
        target = normalize_tool_target(
            tool_name=tool_name,
            integration_name=integration_name,
        )
        normalized_profile_key = normalize_profile_key(credential_profile_key or "default")

        async def _op(repo: CredentialsRepository) -> CredentialBindingMetadata:
            await ensure_profile_exists(repo, profile_id=profile_id)
            if target.integration_alias_mode:
                binding = await resolve_unique_binding_for_alias(
                    repo,
                    profile_id=profile_id,
                    integration_name=target.integration_name,
                    credential_profile_key=normalized_profile_key,
                    credential_name=credential_name,
                    op_name="get",
                )
                if binding is None:
                    row = None
                else:
                    secret = await get_secret(repo, binding)
                    row = (binding, secret)
            else:
                row = await repo.get_secret_plain_binding(
                    profile_id=profile_id,
                    integration_name=target.integration_name,
                    credential_profile_key=normalized_profile_key,
                    tool_name=target.tool_name,
                    credential_name=credential_name,
                )
            if row is None:
                raise CredentialsServiceError(
                    error_code="credentials_not_found",
                    reason="Credential binding not found",
                )
            binding, secret = row
            return to_binding_metadata(binding=binding, key_version=secret.key_version)

        return await self._with_repo(_op)

    async def list(
        self,
        *,
        profile_id: str,
        tool_name: str | None,
        include_inactive: bool = False,
        integration_name: str | None = None,
        credential_profile_key: str | None = None,
    ) -> list[CredentialBindingMetadata]:
        """List credential metadata for profile and optional filters."""

        target = normalize_list_target(
            tool_name=tool_name,
            integration_name=integration_name,
        )
        normalized_profile_key = (
            None if credential_profile_key is None else normalize_profile_key(credential_profile_key)
        )

        async def _op(repo: CredentialsRepository) -> list[CredentialBindingMetadata]:
            await ensure_profile_exists(repo, profile_id=profile_id)
            bindings = await repo.list_bindings(
                profile_id=profile_id,
                integration_name=target.integration_name,
                credential_profile_key=normalized_profile_key,
                tool_name=target.tool_name,
                global_only=target.global_only,
                include_inactive=include_inactive,
            )
            items: list[CredentialBindingMetadata] = []
            for binding in bindings:
                secret = await get_secret(repo, binding)
                items.append(to_binding_metadata(binding=binding, key_version=secret.key_version))
            return items

        return await self._with_repo(_op)
