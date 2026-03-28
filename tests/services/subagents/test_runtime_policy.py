"""Tests for subagent child runtime policy."""

from __future__ import annotations

from afkbot.services.subagents.runtime_policy import SubagentRuntimePolicy
from afkbot.settings import Settings


def test_subagent_runtime_policy_filters_recursive_plugins() -> None:
    """Child settings should remove recursive subagent lifecycle plugins."""

    settings = Settings(
        enabled_tool_plugins=(
            "debug_echo",
            "subagent_run",
            "subagent_wait",
            "subagent_result",
            "file_read",
        )
    )

    resolved = SubagentRuntimePolicy().build_child_settings(settings)

    assert resolved.enabled_tool_plugins == ("debug_echo", "file_read")


def test_subagent_runtime_policy_builds_deterministic_child_identifiers() -> None:
    """Child runtime policy should keep session id and prompt overlay deterministic."""

    policy = SubagentRuntimePolicy()

    assert policy.build_child_session_id(task_id="task-123") == "subagent:task-123"
    assert (
        policy.build_prompt_overlay(subagent_name="researcher", subagent_markdown="# researcher")
        == "Run as a child subagent for the parent agent. "
        "Use the following subagent-specific instructions.\n"
        "Subagent: researcher\n\n"
        "# researcher"
    )
