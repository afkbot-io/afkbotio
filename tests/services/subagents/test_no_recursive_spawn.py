"""Tests for recursive subagent spawn guard."""

from __future__ import annotations

from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.plugins.session_job_run import (
    create_tool as create_session_job_run_tool,
)
from afkbot.services.tools.plugins.subagent_run.plugin import SubagentRunTool
from afkbot.settings import Settings


async def test_subagent_run_forbidden_from_subagent_actor() -> None:
    """Subagent actor should not be able to spawn another subagent."""

    tool = SubagentRunTool(Settings())
    params = tool.parse_params(
        {"prompt": "hello", "subagent_name": "researcher"},
        default_timeout_sec=15,
        max_timeout_sec=900,
    )
    result = await tool.execute(
        ToolContext(profile_id="default", session_id="s-1", run_id=1, actor="subagent"),
        params,
    )

    assert result.ok is False
    assert result.error_code == "subagent_recursive_spawn_forbidden"


async def test_session_job_run_forbidden_from_subagent_actor() -> None:
    """Subagent actor should not be able to spawn session jobs."""

    tool = create_session_job_run_tool(Settings())
    params = tool.parse_params(
        {"jobs": [{"kind": "subagent", "prompt": "hello", "subagent_name": "researcher"}]},
        default_timeout_sec=15,
        max_timeout_sec=900,
    )
    result = await tool.execute(
        ToolContext(profile_id="default", session_id="s-1", run_id=1, actor="subagent"),
        params,
    )

    assert result.ok is False
    assert result.error_code == "session_job_recursive_spawn_forbidden"
