"""Shared trusted turn-context helpers for channel runtimes."""

from __future__ import annotations

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.channels.tool_profiles import normalize_channel_tool_profile


def build_channel_tool_profile_context_overrides(
    tool_profile: object,
) -> TurnContextOverrides | None:
    """Attach channel tool-profile metadata when one explicit narrowing profile is configured."""

    try:
        normalized = normalize_channel_tool_profile(tool_profile)
    except ValueError:
        return None
    if normalized == "inherit":
        return None
    return TurnContextOverrides(runtime_metadata={"channel_tool_profile": normalized})
