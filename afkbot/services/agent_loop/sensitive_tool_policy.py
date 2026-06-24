"""Sensitive tool restrictions for user-facing runtime contexts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from afkbot.services.channel_routing.policy import is_user_facing_transport
from afkbot.services.tools.base import ToolResult

_USER_FACING_BLOCKED_TOOL_NAMES = frozenset(
    {
        "credentials.list",
        "credentials.request",
        "credentials.create",
        "credentials.update",
        "credentials.delete",
    }
)
_CREDENTIAL_PLACEHOLDER_MARKERS = ("${{CRED:", "${CRED_")


def blocked_tool_names_for_runtime(
    *,
    runtime_metadata: dict[str, object] | None,
) -> frozenset[str]:
    """Return sensitive tool names blocked for the current trusted runtime context."""

    if not _is_user_facing_runtime(runtime_metadata):
        return frozenset()
    return _USER_FACING_BLOCKED_TOOL_NAMES


def blocked_tool_result(
    *,
    tool_name: str,
    runtime_metadata: dict[str, object] | None,
) -> ToolResult | None:
    """Return deterministic error for blocked sensitive tools in user-facing runtimes."""

    if tool_name not in blocked_tool_names_for_runtime(runtime_metadata=runtime_metadata):
        return None
    return ToolResult.error(
        error_code="tool_blocked_in_user_channel",
        reason=(
            "Credential inventory and credential management tools are blocked in "
            "user-facing channel conversations. Use CLI or another trusted operator surface."
        ),
    )


def blocked_credential_placeholder_result(
    *,
    tool_name: str,
    params: Mapping[str, object],
    runtime_metadata: dict[str, object] | None,
) -> ToolResult | None:
    """Block credential placeholder expansion in user-facing channel runtimes."""

    if not _is_user_facing_runtime(runtime_metadata):
        return None
    if not _contains_credential_placeholder(params):
        return None
    return ToolResult.error(
        error_code="credential_placeholder_blocked_in_user_channel",
        reason=(
            "Credential placeholders are blocked in user-facing channel conversations. "
            "Use a trusted operator surface or preconfigured channel endpoint credentials."
        ),
        metadata={"tool_name": tool_name},
    )


def _is_user_facing_runtime(runtime_metadata: dict[str, object] | None) -> bool:
    """Return whether runtime metadata clearly identifies an external user-facing ingress."""

    if not isinstance(runtime_metadata, dict):
        return False
    explicit_flag = runtime_metadata.get("user_facing_channel")
    if isinstance(explicit_flag, bool):
        return explicit_flag
    transport = runtime_metadata.get("transport")
    if isinstance(transport, str) and is_user_facing_transport(transport):
        return True
    batch_payload = runtime_metadata.get("channel_ingress_batch")
    if isinstance(batch_payload, dict):
        batch_transport = batch_payload.get("transport")
        if isinstance(batch_transport, str) and is_user_facing_transport(batch_transport):
            return True
    return False


def _contains_credential_placeholder(value: object) -> bool:
    """Return whether nested params contain supported credential placeholder markers."""

    if isinstance(value, str):
        return any(marker in value for marker in _CREDENTIAL_PLACEHOLDER_MARKERS)
    if isinstance(value, Mapping):
        return any(
            _contains_credential_placeholder(key)
            or _contains_credential_placeholder(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return any(_contains_credential_placeholder(item) for item in value)
    return False
