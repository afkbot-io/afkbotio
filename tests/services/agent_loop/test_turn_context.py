"""Tests for turn-scoped context override merging."""

from __future__ import annotations

from afkbot.services.agent_loop.turn_context import TurnContextOverrides, merge_turn_context_overrides


def test_merge_turn_context_overrides_skips_effectively_empty_payload() -> None:
    """Merge helper should return None when no effective override is present."""

    merged = merge_turn_context_overrides(TurnContextOverrides())

    assert merged is None


def test_merge_turn_context_overrides_combines_metadata_prompt_and_thinking() -> None:
    """Merge helper should keep later thinking/planning fields and merge trusted metadata."""

    merged = merge_turn_context_overrides(
        TurnContextOverrides(
            runtime_metadata={"channel": "telegram"},
            cli_approval_surface_enabled=True,
            approved_tool_names=("bash.exec",),
            prompt_overlay="base",
        ),
        TurnContextOverrides(
            approved_tool_names=("file.read", "bash.exec"),
            planning_mode="plan_only",
            execution_planning_mode="off",
            thinking_level="high",
            tool_access_mode="read_only",
            prompt_overlay="overlay",
        ),
    )

    assert merged is not None
    assert merged.runtime_metadata == {"channel": "telegram"}
    assert merged.cli_approval_surface_enabled is True
    assert merged.approved_tool_names == ("bash.exec", "file.read")
    assert merged.prompt_overlay == "base\n\noverlay"
    assert merged.planning_mode == "plan_only"
    assert merged.execution_planning_mode == "off"
    assert merged.thinking_level == "high"
    assert merged.tool_access_mode == "read_only"
