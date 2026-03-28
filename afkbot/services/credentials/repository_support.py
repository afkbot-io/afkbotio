"""Repository-side support helpers for credentials service workflows."""

from __future__ import annotations

from afkbot.models.secret import Secret
from afkbot.models.tool_credential_binding import ToolCredentialBinding
from afkbot.repositories.credentials_repo import CredentialsRepository
from afkbot.services.credentials.errors import CredentialsServiceError


async def ensure_profile_exists(repo: CredentialsRepository, *, profile_id: str) -> None:
    """Raise deterministic error when AFKBOT profile row is missing."""

    if await repo.validate_profile_exists(profile_id):
        return
    raise CredentialsServiceError(error_code="profile_not_found", reason="Profile not found")


async def ensure_profile_key(
    repo: CredentialsRepository,
    *,
    profile_id: str,
    integration_name: str,
    credential_profile_key: str,
) -> None:
    """Create missing credential profile row lazily on first secret write."""

    if (
        await repo.get_profile(
            profile_id=profile_id,
            integration_name=integration_name,
            profile_key=credential_profile_key,
        )
        is not None
    ):
        return
    await repo.create_profile(
        profile_id=profile_id,
        integration_name=integration_name,
        profile_key=credential_profile_key,
        display_name=credential_profile_key,
        is_default=credential_profile_key == "default",
    )


async def resolve_unique_binding_for_alias(
    repo: CredentialsRepository,
    *,
    profile_id: str,
    integration_name: str,
    credential_profile_key: str,
    credential_name: str,
    op_name: str,
) -> ToolCredentialBinding | None:
    """Resolve one binding for integration alias mode or raise on ambiguity."""

    candidates = await repo.list_bindings(
        profile_id=profile_id,
        integration_name=integration_name,
        credential_profile_key=credential_profile_key,
        tool_name=None,
        global_only=False,
        include_inactive=False,
    )
    matches = [item for item in candidates if item.credential_name == credential_name]
    if not matches:
        return None
    if len(matches) > 1:
        raise CredentialsServiceError(
            error_code="credential_binding_conflict",
            reason=(
                f"Multiple bindings match integration alias for credentials.{op_name}; "
                "specify full tool_name."
            ),
            details={
                "integration_name": integration_name,
                "credential_profile_key": credential_profile_key,
                "credential_name": credential_name,
                "matching_tool_names": sorted(
                    {
                        str(item.tool_name).strip() or "<global>"
                        for item in matches
                    }
                ),
            },
        )
    return matches[0]


async def get_secret(repo: CredentialsRepository, binding: ToolCredentialBinding) -> Secret:
    """Load the secret row for one binding or raise deterministic error."""

    secret = await repo.get_secret(binding.secret_id)
    if secret is None:  # pragma: no cover - defensive
        raise CredentialsServiceError(
            error_code="credentials_not_found",
            reason="Secret row not found",
        )
    return secret
