"""Metadata mapping helpers for credentials service responses."""

from __future__ import annotations

from afkbot.models.credential_profile import CredentialProfile
from afkbot.models.tool_credential_binding import ToolCredentialBinding
from afkbot.services.credentials.contracts import (
    CredentialBindingMetadata,
    CredentialProfileMetadata,
)


def to_binding_metadata(
    *,
    binding: ToolCredentialBinding,
    key_version: str,
) -> CredentialBindingMetadata:
    """Convert one binding row plus secret metadata into API-safe metadata."""

    return CredentialBindingMetadata(
        id=binding.id,
        profile_id=binding.profile_id,
        integration_name=binding.integration_name,
        credential_profile_key=binding.credential_profile_key,
        tool_name=binding.tool_name or None,
        credential_name=binding.credential_name,
        key_version=key_version,
        is_active=binding.is_active,
        created_at=binding.created_at,
        updated_at=binding.updated_at,
    )


def to_profile_metadata(row: CredentialProfile) -> CredentialProfileMetadata:
    """Convert one credential profile row into contract metadata."""

    return CredentialProfileMetadata(
        id=row.id,
        profile_id=row.profile_id,
        integration_name=row.integration_name,
        profile_key=row.profile_key,
        display_name=row.display_name,
        is_default=row.is_default,
        is_active=row.is_active,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
