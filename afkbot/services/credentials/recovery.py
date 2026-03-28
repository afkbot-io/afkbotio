"""Helpers for deterministic credential recovery guidance."""

from __future__ import annotations


def build_missing_credential_reason(
    *,
    integration_name: str,
    credential_name: str,
    credential_profile_key: str,
) -> str:
    """Return an English recovery hint for one missing credential."""

    return (
        f"Missing credential '{credential_name}' for integration '{integration_name}' "
        f"and credential profile '{credential_profile_key}'. "
        "Call credentials.request without a value to request secure input, then retry the original tool."
    )


def build_missing_credential_metadata(
    *,
    integration_name: str,
    credential_name: str,
    credential_profile_key: str,
    tool_name: str,
    extra_details: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return structured metadata for secure credential recovery."""

    metadata: dict[str, object] = {
        "integration_name": integration_name,
        "tool_name": tool_name,
        "credential_profile_key": credential_profile_key,
        "credential_name": credential_name,
        "suggested_tool_name": "credentials.request",
        "suggested_tool_params": {
            "app_name": integration_name,
            "profile_name": credential_profile_key,
            "credential_slug": credential_name,
        },
        "suggested_command": (
            "credentials.request("
            f"app_name='{integration_name}', "
            f"profile_name='{credential_profile_key}', "
            f"credential_slug='{credential_name}')"
        ),
    }
    if extra_details:
        metadata.update({str(key): value for key, value in extra_details.items()})
    return metadata


def build_profile_selection_reason(
    *,
    integration_name: str,
    credential_name: str,
) -> str:
    """Return an English recovery hint for ambiguous credential profiles."""

    return (
        f"Multiple credential profiles are available for integration '{integration_name}' "
        f"and credential '{credential_name}'. Choose one credential profile and retry the original tool."
    )
