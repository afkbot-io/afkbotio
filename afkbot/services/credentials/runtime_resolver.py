"""Runtime credential resolution helpers shared by app-integrations."""

from __future__ import annotations

from collections.abc import Callable

from afkbot.models.secret import Secret
from afkbot.models.tool_credential_binding import ToolCredentialBinding
from afkbot.repositories.credentials_repo import CredentialsRepository
from afkbot.services.credentials.errors import CredentialsServiceError
from afkbot.services.credentials.recovery import (
    build_missing_credential_metadata,
    build_missing_credential_reason,
    build_profile_selection_reason,
)


class CredentialRuntimeResolver:
    """Resolve active credential bindings using app runtime fallback rules."""

    def __init__(self, repo: CredentialsRepository) -> None:
        self._repo = repo

    async def resolve_effective_profile_key(
        self,
        *,
        profile_id: str,
        integration_name: str,
        credential_profile_key: str | None,
        tool_name: str | None,
        credential_name: str,
        normalize_profile_key: Callable[[str], str],
    ) -> str:
        """Resolve effective credential profile key for one runtime lookup."""

        if credential_profile_key is not None and credential_profile_key.strip():
            normalized_profile_key = normalize_profile_key(credential_profile_key)
            integration_profile = await self._repo.get_profile(
                profile_id=profile_id,
                integration_name=integration_name,
                profile_key=normalized_profile_key,
            )
            if integration_profile is not None:
                return normalized_profile_key
            if integration_name != "global":
                global_profile = await self._repo.get_profile(
                    profile_id=profile_id,
                    integration_name="global",
                    profile_key=normalized_profile_key,
                )
                if global_profile is not None:
                    return normalized_profile_key
            available_profile_keys = await self._list_available_profile_keys(
                profile_id=profile_id,
                integration_name=integration_name,
            )
            if available_profile_keys:
                raise CredentialsServiceError(
                    error_code="credential_profile_required",
                    reason=(
                        "Requested credential profile is unavailable; "
                        "explicit credential_profile_key is required"
                    ),
                    details={
                        "integration_name": integration_name,
                        "tool_name": tool_name,
                        "credential_name": credential_name,
                        "requested_profile_key": normalized_profile_key,
                        "available_profile_keys": list(available_profile_keys),
                    },
                )
            return normalized_profile_key

        profile_key = await self._pick_default_or_single_profile_key(
            profile_id=profile_id,
            integration_name=integration_name,
            tool_name=tool_name,
            credential_name=credential_name,
        )
        if profile_key is not None:
            return profile_key
        if integration_name != "global":
            profile_key = await self._pick_default_or_single_profile_key(
                profile_id=profile_id,
                integration_name="global",
                tool_name=tool_name,
                credential_name=credential_name,
            )
            if profile_key is not None:
                return profile_key
        return "default"

    async def resolve_secret_plain_binding_for_app_tool(
        self,
        *,
        profile_id: str,
        normalized_integration: str,
        normalized_tool_name: str,
        credential_profile_key: str | None,
        credential_name: str,
        normalize_profile_key: Callable[[str], str],
    ) -> tuple[ToolCredentialBinding, Secret]:
        """Resolve binding row for app tools using runtime fallback order."""

        normalized_profile_key = await self.resolve_effective_profile_key(
            profile_id=profile_id,
            integration_name=normalized_integration,
            credential_profile_key=credential_profile_key,
            tool_name=normalized_tool_name,
            credential_name=credential_name,
            normalize_profile_key=normalize_profile_key,
        )

        row = await self._repo.get_secret_plain_binding(
            profile_id=profile_id,
            integration_name=normalized_integration,
            credential_profile_key=normalized_profile_key,
            tool_name=normalized_tool_name,
            credential_name=credential_name,
        )
        if row is None:
            row = await self._repo.get_secret_plain_binding(
                profile_id=profile_id,
                integration_name=normalized_integration,
                credential_profile_key=normalized_profile_key,
                tool_name=None,
                credential_name=credential_name,
            )
        if row is None and normalized_integration != "global":
            row = await self._repo.get_secret_plain_binding(
                profile_id=profile_id,
                integration_name="global",
                credential_profile_key=normalized_profile_key,
                tool_name=normalized_tool_name,
                credential_name=credential_name,
            )
        if row is None and normalized_integration != "global":
            row = await self._repo.get_secret_plain_binding(
                profile_id=profile_id,
                integration_name="global",
                credential_profile_key=normalized_profile_key,
                tool_name=None,
                credential_name=credential_name,
            )
        if row is not None:
            return row

        total = await self._repo.count_bindings_for_credential(
            profile_id=profile_id,
            integration_name=normalized_integration,
            credential_name=credential_name,
        )
        if normalized_integration != "global":
            total += await self._repo.count_bindings_for_credential(
                profile_id=profile_id,
                integration_name="global",
                credential_name=credential_name,
            )
        if total > 0:
            raise CredentialsServiceError(
                error_code="credential_binding_conflict",
                reason="Credential exists in another credential profile",
                details={
                    "integration_name": normalized_integration,
                    "tool_name": normalized_tool_name,
                    "credential_profile_key": normalized_profile_key,
                    "credential_name": credential_name,
                },
            )
        raise CredentialsServiceError(
            error_code="credentials_missing",
            reason=build_missing_credential_reason(
                integration_name=normalized_integration,
                credential_name=credential_name,
                credential_profile_key=normalized_profile_key,
            ),
            details=build_missing_credential_metadata(
                integration_name=normalized_integration,
                credential_name=credential_name,
                credential_profile_key=normalized_profile_key,
                tool_name=normalized_tool_name,
            ),
        )

    async def _pick_default_or_single_profile_key(
        self,
        *,
        profile_id: str,
        integration_name: str,
        tool_name: str | None,
        credential_name: str,
    ) -> str | None:
        profiles = await self._repo.list_profiles(
            profile_id=profile_id,
            integration_name=integration_name,
            include_inactive=False,
        )
        if not profiles:
            return None
        defaults = [row.profile_key for row in profiles if row.is_default]
        if len(defaults) == 1:
            return defaults[0]
        profile_keys = sorted({row.profile_key for row in profiles})
        if len(profile_keys) == 1:
            return profile_keys[0]
        raise CredentialsServiceError(
            error_code="credential_profile_required",
            reason=build_profile_selection_reason(
                integration_name=integration_name,
                credential_name=credential_name,
            ),
            details={
                "integration_name": integration_name,
                "tool_name": tool_name,
                "credential_name": credential_name,
                "available_profile_keys": profile_keys,
            },
        )

    async def _list_available_profile_keys(
        self,
        *,
        profile_id: str,
        integration_name: str,
    ) -> tuple[str, ...]:
        """Return active profile keys visible to runtime fallback resolution."""

        rows = await self._repo.list_profiles(
            profile_id=profile_id,
            integration_name=integration_name,
            include_inactive=False,
        )
        profile_keys = {row.profile_key for row in rows}
        if integration_name != "global":
            global_rows = await self._repo.list_profiles(
                profile_id=profile_id,
                integration_name="global",
                include_inactive=False,
            )
            profile_keys.update(row.profile_key for row in global_rows)
        return tuple(sorted(profile_keys))
