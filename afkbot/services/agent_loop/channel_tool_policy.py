"""Channel-level tool-profile narrowing on top of profile policy."""

from __future__ import annotations

from afkbot.services.channels.tool_profiles import (
    ChannelToolProfile,
    allowed_tool_names_for_channel_profile,
    normalize_channel_tool_profile,
)
from afkbot.services.tools.base import ToolResult


def filter_tool_names_for_runtime(
    *,
    tool_names: tuple[str, ...],
    runtime_metadata: dict[str, object] | None,
) -> tuple[str, ...]:
    """Filter one visible tool set through the active channel tool profile."""

    tool_profile = _resolve_channel_tool_profile(runtime_metadata)
    if tool_profile == "inherit":
        return tool_names
    allowed_tool_names = allowed_tool_names_for_channel_profile(tool_profile)
    if allowed_tool_names is None:
        return tool_names
    allowed_set = set(allowed_tool_names)
    return tuple(name for name in tool_names if name in allowed_set)


def blocked_tool_result_for_runtime(
    *,
    tool_name: str,
    runtime_metadata: dict[str, object] | None,
) -> ToolResult | None:
    """Return a deterministic blocked result when the channel tool profile denies the tool."""

    tool_profile = _resolve_channel_tool_profile(runtime_metadata)
    if tool_profile == "inherit":
        return None
    allowed_tool_names = allowed_tool_names_for_channel_profile(tool_profile)
    if allowed_tool_names is None or tool_name in allowed_tool_names:
        return None
    return ToolResult.error(
        error_code="tool_blocked_by_channel_profile",
        reason=(
            f"Tool `{tool_name}` is blocked by channel tool profile `{tool_profile}`. "
            "Use a more trusted surface or update the channel configuration."
        ),
        metadata={"channel_tool_profile": tool_profile},
    )


def resolve_channel_tool_profile_for_runtime(
    *,
    runtime_metadata: dict[str, object] | None,
) -> ChannelToolProfile:
    """Public accessor for inspection/debug flows."""

    return _resolve_channel_tool_profile(runtime_metadata)


def _resolve_channel_tool_profile(
    runtime_metadata: dict[str, object] | None,
) -> ChannelToolProfile:
    if not isinstance(runtime_metadata, dict):
        return "inherit"
    raw_value = runtime_metadata.get("channel_tool_profile")
    try:
        return normalize_channel_tool_profile(raw_value)
    except ValueError:
        return "inherit"
