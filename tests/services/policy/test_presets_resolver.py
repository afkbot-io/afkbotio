"""Tests for policy preset resolver."""

from __future__ import annotations

import pytest

from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS
from afkbot.services.policy import (
    PolicySelection,
    PolicyCapabilityId,
    PolicyPresetLevel,
    parse_capability_ids,
    parse_preset_level,
    resolve_policy,
)


def test_parse_preset_level_and_capabilities() -> None:
    """Preset and capability parsers should normalize valid values."""

    assert parse_preset_level("STRICT") is PolicyPresetLevel.STRICT
    assert parse_preset_level("hard") is PolicyPresetLevel.STRICT
    assert parse_preset_level("light") is PolicyPresetLevel.SIMPLE
    caps = parse_capability_ids(["memory", "http", "memory"])
    assert caps == (PolicyCapabilityId.MEMORY, PolicyCapabilityId.HTTP)


def test_parse_capability_ids_maps_legacy_aliases_to_apps() -> None:
    """Legacy email/telegram ids should normalize to the app capability."""

    caps = parse_capability_ids(["email", "telegram", "apps"])
    assert caps == (PolicyCapabilityId.APPS,)


def test_parse_preset_level_rejects_unknown_value() -> None:
    """Unknown preset should raise explicit validation error."""

    with pytest.raises(ValueError, match="Unknown policy preset"):
        parse_preset_level("ultra")


def test_resolve_policy_filters_by_available_tools() -> None:
    """Resolver should keep only tools that exist in runtime registry."""

    selection = PolicySelection(
        enabled=True,
        preset=PolicyPresetLevel.MEDIUM,
        capabilities=(PolicyCapabilityId.MEMORY, PolicyCapabilityId.HTTP),
    )
    resolved = resolve_policy(
        selection=selection,
        available_tool_names=("memory.search", "memory.upsert", "debug.echo"),
    )
    assert resolved.enabled is True
    assert resolved.allowed_tools == ("memory.search", "memory.upsert")


def test_resolve_policy_disabled_mode() -> None:
    """Disabled policy should preserve configured capabilities/tools for later re-enable."""

    selection = PolicySelection(
        enabled=False,
        preset=PolicyPresetLevel.STRICT,
        capabilities=(PolicyCapabilityId.HTTP,),
    )
    resolved = resolve_policy(
        selection=selection,
        available_tool_names=("http.request", "memory.search"),
    )
    assert resolved.enabled is False
    assert resolved.allowed_tools == ("http.request",)
    assert resolved.capabilities == (PolicyCapabilityId.HTTP,)


def test_resolve_policy_keeps_explicit_empty_capabilities() -> None:
    """Enabled policy with explicit empty capabilities should resolve to deny-all tools."""

    selection = PolicySelection(
        enabled=True,
        preset=PolicyPresetLevel.SIMPLE,
        capabilities=(),
    )
    resolved = resolve_policy(
        selection=selection,
        available_tool_names=("memory.search", "http.request"),
    )
    assert resolved.enabled is True
    assert resolved.capabilities == ()
    assert resolved.allowed_tools == ()


def test_resolve_policy_maps_new_tool_capabilities() -> None:
    """Resolver should include new web/browser/skills/apps tools when selected."""

    selection = PolicySelection(
        enabled=True,
        preset=PolicyPresetLevel.MEDIUM,
        capabilities=(
            PolicyCapabilityId.WEB,
            PolicyCapabilityId.BROWSER,
            PolicyCapabilityId.SKILLS,
            PolicyCapabilityId.APPS,
        ),
    )
    resolved = resolve_policy(
        selection=selection,
        available_tool_names=(
            "app.list",
            "app.run",
            "browser.control",
            "debug.echo",
            "skill.marketplace.install",
            "skill.profile.list",
            "web.fetch",
            "web.search",
        ),
    )
    assert resolved.allowed_tools == (
        "app.list",
        "app.run",
        "browser.control",
        "skill.marketplace.install",
        "skill.profile.list",
        "web.fetch",
        "web.search",
    )


def test_resolve_policy_adds_wildcard_for_mcp_runtime_capability() -> None:
    """MCP runtime capability should preserve wildcard allow rules for profile-aware bridges."""

    # Arrange
    selection = PolicySelection(
        enabled=True,
        preset=PolicyPresetLevel.MEDIUM,
        capabilities=(PolicyCapabilityId.MCP,),
    )

    # Act
    resolved = resolve_policy(
        selection=selection,
        available_tool_names=(),
    )

    # Assert
    assert resolved.allowed_tools == ("mcp.*",)


def test_files_capability_no_longer_grants_skill_management_tools() -> None:
    """files capability should not implicitly grant skill.profile/marketplace rights."""

    selection = PolicySelection(
        enabled=True,
        preset=PolicyPresetLevel.MEDIUM,
        capabilities=(PolicyCapabilityId.FILES,),
    )
    resolved = resolve_policy(
        selection=selection,
        available_tool_names=("file.read", "skill.profile.list", "skill.marketplace.list"),
    )
    assert resolved.allowed_tools == ("file.read",)


def test_files_capability_includes_diffs_render() -> None:
    """files capability should include diffs.render for file mutation review flows."""

    selection = PolicySelection(
        enabled=True,
        preset=PolicyPresetLevel.MEDIUM,
        capabilities=(PolicyCapabilityId.FILES,),
    )
    resolved = resolve_policy(
        selection=selection,
        available_tool_names=("diffs.render", "file.read"),
    )
    assert resolved.allowed_tools == ("diffs.render", "file.read")


@pytest.mark.parametrize(
    ("preset",),
    [
        (PolicyPresetLevel.SIMPLE,),
        (PolicyPresetLevel.MEDIUM,),
        (PolicyPresetLevel.STRICT,),
    ],
)
def test_resolve_policy_uses_500_iteration_defaults_for_every_preset(
    preset: PolicyPresetLevel,
) -> None:
    """Policy presets should no longer clamp fresh profiles to 15, 30, or 50 iterations."""

    # Arrange
    selection = PolicySelection(
        enabled=True,
        preset=preset,
        capabilities=(),
    )

    # Act
    resolved = resolve_policy(
        selection=selection,
        available_tool_names=(),
    )

    # Assert
    assert resolved.max_iterations_main == DEFAULT_LLM_MAX_ITERATIONS
    assert resolved.max_iterations_subagent == DEFAULT_LLM_MAX_ITERATIONS
