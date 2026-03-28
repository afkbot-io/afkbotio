"""Runtime-oriented credential resolution mixin for app/tool execution."""

from __future__ import annotations

import builtins
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from afkbot.repositories.credentials_repo import CredentialsRepository
from afkbot.services.credentials.contracts import CredentialBindingMetadata
from afkbot.services.credentials.errors import CredentialsServiceError
from afkbot.services.credentials.metadata import to_binding_metadata
from afkbot.services.credentials.repository_support import ensure_profile_exists, ensure_profile_key
from afkbot.services.credentials.runtime_resolver import CredentialRuntimeResolver
from afkbot.services.credentials.targets import (
    normalize_integration_name,
    normalize_profile_key,
    normalize_tool_name,
    resolve_integration_name,
    validate_credential_name,
)
from afkbot.services.credentials.vault import CredentialsVault, CredentialsVaultError

TRepoValue = TypeVar("TRepoValue")


class CredentialsRuntimeMixin:
    """Provide runtime credential selection methods on top of repository helpers."""

    if TYPE_CHECKING:
        _vault: CredentialsVault

        async def _with_repo(
            self,
            op: Callable[[CredentialsRepository], Awaitable[TRepoValue]],
        ) -> TRepoValue: ...

        async def list(
            self,
            *,
            profile_id: str,
            tool_name: str | None,
            include_inactive: bool = False,
            integration_name: str | None = None,
            credential_profile_key: str | None = None,
        ) -> list[CredentialBindingMetadata]: ...

    async def resolve_plaintext_for_app_tool(
        self,
        *,
        profile_id: str,
        tool_name: str,
        integration_name: str,
        credential_profile_key: str | None,
        credential_name: str,
    ) -> str:
        """Resolve plaintext credential for app tool execution."""

        validate_credential_name(credential_name)
        normalized_tool_name = normalize_tool_name(tool_name)
        normalized_integration = normalize_integration_name(integration_name)
        if normalized_tool_name is None:
            raise CredentialsServiceError(
                error_code="credentials_invalid_name",
                reason="Invalid tool name",
            )

        async def _op(repo: CredentialsRepository) -> str:
            await ensure_profile_exists(repo, profile_id=profile_id)
            _, secret = await CredentialRuntimeResolver(repo).resolve_secret_plain_binding_for_app_tool(
                profile_id=profile_id,
                normalized_integration=normalized_integration,
                normalized_tool_name=normalized_tool_name,
                credential_profile_key=credential_profile_key,
                credential_name=credential_name,
                normalize_profile_key=normalize_profile_key,
            )
            try:
                return self._vault.decrypt(secret.encrypted_value)
            except CredentialsVaultError as exc:
                raise CredentialsServiceError(error_code=exc.error_code, reason=exc.reason) from exc

        return await self._with_repo(_op)

    async def resolve_metadata_for_app_tool(
        self,
        *,
        profile_id: str,
        tool_name: str,
        integration_name: str,
        credential_profile_key: str | None,
        credential_name: str,
    ) -> CredentialBindingMetadata:
        """Resolve binding metadata for app tool execution using runtime fallback rules."""

        validate_credential_name(credential_name)
        normalized_tool_name = normalize_tool_name(tool_name)
        normalized_integration = normalize_integration_name(integration_name)
        if normalized_tool_name is None:
            raise CredentialsServiceError(
                error_code="credentials_invalid_name",
                reason="Invalid tool name",
            )

        async def _op(repo: CredentialsRepository) -> CredentialBindingMetadata:
            await ensure_profile_exists(repo, profile_id=profile_id)
            binding, secret = await CredentialRuntimeResolver(repo).resolve_secret_plain_binding_for_app_tool(
                profile_id=profile_id,
                normalized_integration=normalized_integration,
                normalized_tool_name=normalized_tool_name,
                credential_profile_key=credential_profile_key,
                credential_name=credential_name,
                normalize_profile_key=normalize_profile_key,
            )
            return to_binding_metadata(binding=binding, key_version=secret.key_version)

        return await self._with_repo(_op)

    async def resolve_effective_profile_key_for_app_tool(
        self,
        *,
        profile_id: str,
        tool_name: str,
        integration_name: str,
        credential_profile_key: str | None,
        credential_name: str,
    ) -> str:
        """Resolve effective credential profile key using app runtime selection rules."""

        validate_credential_name(credential_name)
        normalized_tool_name = normalize_tool_name(tool_name)
        normalized_integration = normalize_integration_name(integration_name)
        if normalized_tool_name is None:
            raise CredentialsServiceError(
                error_code="credentials_invalid_name",
                reason="Invalid tool name",
            )

        async def _op(repo: CredentialsRepository) -> str:
            await ensure_profile_exists(repo, profile_id=profile_id)
            return await CredentialRuntimeResolver(repo).resolve_effective_profile_key(
                profile_id=profile_id,
                integration_name=normalized_integration,
                credential_profile_key=credential_profile_key,
                tool_name=normalized_tool_name,
                credential_name=credential_name,
                normalize_profile_key=normalize_profile_key,
            )

        return await self._with_repo(_op)

    async def list_bindings_for_app_runtime(
        self,
        *,
        profile_id: str,
        tool_name: str,
        integration_name: str,
        credential_profile_key: str | None,
        include_inactive: bool = False,
    ) -> builtins.list[CredentialBindingMetadata]:
        """List bindings visible to app runtime, including global fallback rows."""

        normalized_tool_name = normalize_tool_name(tool_name)
        if normalized_tool_name is None:
            raise CredentialsServiceError(
                error_code="credentials_invalid_name",
                reason="Invalid tool name",
            )
        primary = await self.list(
            profile_id=profile_id,
            tool_name=None,
            integration_name=integration_name,
            credential_profile_key=credential_profile_key,
            include_inactive=include_inactive,
        )
        fallback: list[CredentialBindingMetadata] = []
        if integration_name.strip().lower() != "global":
            fallback = await self.list(
                profile_id=profile_id,
                tool_name=None,
                integration_name="global",
                credential_profile_key=credential_profile_key,
                include_inactive=include_inactive,
            )

        merged: list[CredentialBindingMetadata] = []
        seen: set[tuple[str, str, str | None, str, bool]] = set()
        for item in [*primary, *fallback]:
            if item.tool_name not in {None, normalized_tool_name}:
                continue
            key = (
                item.integration_name,
                item.credential_profile_key,
                item.tool_name,
                item.credential_name,
                item.is_active,
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    async def resolve_plaintext_for_tool(
        self,
        *,
        profile_id: str,
        tool_name: str,
        credential_name: str,
    ) -> str:
        """Resolve plaintext credential for generic tool execution helper."""

        validate_credential_name(credential_name)
        normalized_tool_name = normalize_tool_name(tool_name)
        normalized_integration = resolve_integration_name(
            integration_name=None,
            tool_name=normalized_tool_name,
        )

        async def _op(repo: CredentialsRepository) -> str:
            await ensure_profile_exists(repo, profile_id=profile_id)
            await ensure_profile_key(
                repo,
                profile_id=profile_id,
                integration_name=normalized_integration,
                credential_profile_key="default",
            )
            row = await repo.get_secret_plain_binding(
                profile_id=profile_id,
                integration_name=normalized_integration,
                credential_profile_key="default",
                tool_name=normalized_tool_name,
                credential_name=credential_name,
            )
            if row is None:
                row = await repo.get_secret_plain_binding(
                    profile_id=profile_id,
                    integration_name=normalized_integration,
                    credential_profile_key="default",
                    tool_name=None,
                    credential_name=credential_name,
                )
            if row is None:
                raise CredentialsServiceError(
                    error_code="credentials_not_found",
                    reason="Credential binding not found",
                )
            _, secret = row
            try:
                return self._vault.decrypt(secret.encrypted_value)
            except CredentialsVaultError as exc:
                raise CredentialsServiceError(error_code=exc.error_code, reason=exc.reason) from exc

        return await self._with_repo(_op)
