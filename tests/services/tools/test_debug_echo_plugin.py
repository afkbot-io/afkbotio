"""Tests for debug echo tool plugin behavior."""

from __future__ import annotations

from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.plugins.debug_echo import create_tool
from afkbot.settings import Settings


async def test_debug_echo_execute_uses_context_profile_id() -> None:
    """Debug echo should return context profile id, not params profile_key."""

    tool = create_tool(Settings())
    params = tool.parse_params(
        {"message": "hello", "profile_key": "other-profile"},
        default_timeout_sec=5,
        max_timeout_sec=20,
    )
    result = await tool.execute(
        ToolContext(profile_id="profile-from-context", session_id="s-1", run_id=1),
        params,
    )

    assert result.ok is True
    assert result.payload["message"] == "hello"
    assert result.payload["timeout_sec"] == 5
    assert result.payload["profile_id"] == "profile-from-context"
