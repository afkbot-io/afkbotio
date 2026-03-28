"""Normalization helpers for credentials tool and integration targets."""

from __future__ import annotations

import re
from dataclasses import dataclass

from afkbot.services.credentials.errors import CredentialsServiceError

NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


@dataclass(frozen=True, slots=True)
class CredentialToolTarget:
    """Normalized create/get/update/delete target for one credential binding."""

    tool_name: str | None
    integration_name: str
    integration_alias_mode: bool


@dataclass(frozen=True, slots=True)
class CredentialListTarget:
    """Normalized list target for one credentials.list query."""

    tool_name: str | None
    integration_name: str | None
    global_only: bool


def validate_credential_name(credential_name: str) -> None:
    """Validate credential slug/profile keys shared across credentials flows."""

    if NAME_RE.fullmatch(credential_name) is not None:
        return
    raise CredentialsServiceError(
        error_code="credentials_invalid_name",
        reason="Invalid credential name",
    )


def normalize_tool_name(tool_name: str | None) -> str | None:
    """Normalize optional tool name while preserving explicit global scope."""

    if tool_name is None:
        return None
    value = tool_name.strip()
    return value or None


def normalize_profile_key(profile_key: str) -> str:
    """Normalize one credential profile key or raise deterministic error."""

    value = profile_key.strip()
    if not value:
        raise CredentialsServiceError(
            error_code="credential_profile_required",
            reason="credential_profile_key is required",
        )
    if NAME_RE.fullmatch(value) is None:
        raise CredentialsServiceError(
            error_code="credentials_invalid_name",
            reason="Invalid credential profile key",
        )
    return value


def normalize_integration_name(integration_name: str) -> str:
    """Normalize integration/app name to lowercase-safe identifier."""

    value = integration_name.strip().lower()
    if not value:
        raise CredentialsServiceError(
            error_code="credentials_invalid_name",
            reason="Invalid integration name",
        )
    if NAME_RE.fullmatch(value) is None:
        raise CredentialsServiceError(
            error_code="credentials_invalid_name",
            reason="Invalid integration name",
        )
    return value


def resolve_integration_name(*, integration_name: str | None, tool_name: str | None) -> str:
    """Resolve integration name from explicit app or `tool.root` convention."""

    if integration_name is not None and integration_name.strip():
        return normalize_integration_name(integration_name)
    if tool_name is None:
        return "global"
    root = tool_name.split(".", 1)[0].strip().lower()
    return normalize_integration_name(root or "global")


def normalize_tool_target(
    *,
    tool_name: str | None,
    integration_name: str | None,
) -> CredentialToolTarget:
    """Normalize CRUD/get target with support for integration alias in tool_name."""

    normalized_tool_name = normalize_tool_name(tool_name)
    if integration_name is not None and integration_name.strip():
        return CredentialToolTarget(
            tool_name=normalized_tool_name,
            integration_name=normalize_integration_name(integration_name),
            integration_alias_mode=False,
        )
    if normalized_tool_name is not None and "." not in normalized_tool_name:
        return CredentialToolTarget(
            tool_name=None,
            integration_name=normalize_integration_name(normalized_tool_name),
            integration_alias_mode=True,
        )
    return CredentialToolTarget(
        tool_name=normalized_tool_name,
        integration_name=resolve_integration_name(
            integration_name=None,
            tool_name=normalized_tool_name,
        ),
        integration_alias_mode=False,
    )


def normalize_list_target(
    *,
    tool_name: str | None,
    integration_name: str | None,
) -> CredentialListTarget:
    """Normalize list filters with support for integration alias in tool_name."""

    normalized_tool_name = normalize_tool_name(tool_name)
    global_only = tool_name is not None and normalized_tool_name is None
    if integration_name is not None and integration_name.strip():
        return CredentialListTarget(
            tool_name=normalized_tool_name,
            integration_name=normalize_integration_name(integration_name),
            global_only=global_only,
        )
    if global_only:
        return CredentialListTarget(tool_name=None, integration_name=None, global_only=True)
    if normalized_tool_name is None:
        return CredentialListTarget(tool_name=None, integration_name=None, global_only=False)
    if "." not in normalized_tool_name:
        return CredentialListTarget(
            tool_name=None,
            integration_name=normalize_integration_name(normalized_tool_name),
            global_only=False,
        )
    return CredentialListTarget(
        tool_name=normalized_tool_name,
        integration_name=resolve_integration_name(
            integration_name=None,
            tool_name=normalized_tool_name,
        ),
        global_only=False,
    )
