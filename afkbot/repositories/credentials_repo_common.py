"""Shared SQL helpers for credentials repository mixins."""

from __future__ import annotations

from sqlalchemy import Select, and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from afkbot.models.credential_profile import CredentialProfile
from afkbot.models.tool_credential_binding import ToolCredentialBinding


def normalize_tool_name(tool_name: str | None) -> str:
    """Normalize nullable tool binding scope into stored string form."""

    return (tool_name or "").strip()


def match_binding_identity(
    *,
    profile_id: str,
    integration_name: str,
    credential_profile_key: str,
    tool_name: str,
    credential_name: str,
) -> ColumnElement[bool]:
    """Build deterministic binding identity predicate."""

    return and_(
        ToolCredentialBinding.profile_id == profile_id,
        ToolCredentialBinding.integration_name == integration_name,
        ToolCredentialBinding.credential_profile_key == credential_profile_key,
        ToolCredentialBinding.credential_name == credential_name,
        ToolCredentialBinding.tool_name == tool_name,
    )


async def clear_other_default_profiles(
    *,
    session: AsyncSession,
    profile_id: str,
    integration_name: str,
    except_profile_key: str,
) -> None:
    """Clear other active default profiles for the same integration."""

    statement: Select[tuple[CredentialProfile]] = select(CredentialProfile).where(
        CredentialProfile.profile_id == profile_id,
        CredentialProfile.integration_name == integration_name,
        CredentialProfile.profile_key != except_profile_key,
        CredentialProfile.is_active.is_(True),
        CredentialProfile.is_default.is_(True),
    )
    for row in (await session.execute(statement)).scalars().all():
        row.is_default = False
    await session.flush()
