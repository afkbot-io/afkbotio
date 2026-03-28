"""Sensitive tool restrictions for user-facing runtime contexts."""

from __future__ import annotations

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
