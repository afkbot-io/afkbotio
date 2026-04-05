"""Tests for ToolRegistry loading and lookups."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


def test_registry_from_settings_loads_default_plugin(tmp_path: Path) -> None:
    """Default settings should load all built-in default plugins."""

    settings = Settings(root_dir=tmp_path)
    registry = ToolRegistry.from_settings(settings)

    assert registry.list_names() == (
        "app.list",
        "app.run",
        "automation.create",
        "automation.delete",
        "automation.get",
        "automation.list",
        "automation.update",
        "bash.exec",
        "browser.control",
        "credentials.create",
        "credentials.delete",
        "credentials.list",
        "credentials.request",
        "credentials.update",
        "debug.echo",
        "diffs.render",
        "file.edit",
        "file.list",
        "file.read",
        "file.search",
        "file.write",
        "http.request",
        "mcp.profile.delete",
        "mcp.profile.get",
        "mcp.profile.list",
        "mcp.profile.upsert",
        "mcp.profile.validate",
        "memory.delete",
        "memory.digest",
        "memory.list",
        "memory.promote",
        "memory.search",
        "memory.upsert",
        "skill.marketplace.install",
        "skill.marketplace.list",
        "skill.marketplace.search",
        "skill.profile.delete",
        "skill.profile.get",
        "skill.profile.list",
        "skill.profile.upsert",
        "subagent.profile.delete",
        "subagent.profile.get",
        "subagent.profile.list",
        "subagent.profile.upsert",
        "subagent.result",
        "subagent.run",
        "subagent.wait",
        "task.board",
        "task.comment.add",
        "task.comment.list",
        "task.create",
        "task.dependency.add",
        "task.dependency.list",
        "task.dependency.remove",
        "task.event.list",
        "task.flow.create",
        "task.flow.get",
        "task.flow.list",
        "task.get",
        "task.inbox",
        "task.list",
        "task.review.approve",
        "task.review.list",
        "task.review.request_changes",
        "task.run.get",
        "task.run.list",
        "task.stale.list",
        "task.stale.sweep",
        "task.update",
        "web.fetch",
        "web.search",
    )
    assert registry.get("debug.echo") is not None
    assert registry.get("diffs.render") is not None
    assert registry.get("subagent.run") is not None
    assert registry.get("app.list") is not None
    assert registry.get("app.run") is not None
    assert registry.get("skill.marketplace.list") is not None
    assert registry.get("skill.marketplace.install") is not None
    assert registry.get("skill.marketplace.search") is not None
    assert registry.get("web.search") is not None
    assert registry.get("web.fetch") is not None
    assert registry.get("browser.control") is not None
    assert registry.get("mcp.profile.upsert") is not None
    assert registry.get("task.board") is not None
    assert registry.get("task.comment.add") is not None
    assert registry.get("task.comment.list") is not None
    assert registry.get("task.event.list") is not None
    assert registry.get("task.review.list") is not None
    assert registry.get("task.review.approve") is not None
    assert registry.get("task.flow.create") is not None
    assert registry.get("task.inbox") is not None
    assert registry.get("task.stale.list") is not None
    assert registry.get("task.stale.sweep") is not None
    assert registry.get("task.run.list") is not None
    assert registry.get("mcp.debug.echo") is None


def test_registry_rejects_unknown_plugin() -> None:
    """Unknown plugin should fail fast at registry construction time."""

    settings = Settings()
    with pytest.raises(ValueError, match="Unknown tool plugin"):
        ToolRegistry.from_plugins(("missing_plugin",), settings=settings)


def test_registry_from_profile_settings_adds_runtime_mcp_tools_when_profile_has_remote_servers(
    tmp_path: Path,
) -> None:
    """Profile-aware registry should expose runtime MCP bridge tools for eligible MCP configs."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "mcp.json").write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server": "demo",
                        "transport": "http",
                        "url": "https://demo.example.com/mcp",
                        "capabilities": ["tools"],
                        "env_refs": [],
                        "secret_refs": [],
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(root_dir=tmp_path)

    # Act
    registry = ToolRegistry.from_profile_settings(settings, profile_id="default")

    # Assert
    assert registry.get("mcp.tools.list") is not None
    assert registry.get("mcp.tools.call") is not None


def test_registry_from_profile_settings_skips_runtime_mcp_when_bridge_disabled(
    tmp_path: Path,
) -> None:
    """Profile-aware registry should not expose MCP bridge tools when runtime MCP is disabled."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "mcp.json").write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server": "demo",
                        "transport": "http",
                        "url": "https://demo.example.com/mcp",
                        "capabilities": ["tools"],
                        "env_refs": [],
                        "secret_refs": [],
                        "enabled": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(root_dir=tmp_path, mcp_runtime_enabled=False)

    # Act
    registry = ToolRegistry.from_profile_settings(settings, profile_id="default")

    # Assert
    assert registry.get("mcp.tools.list") is None
    assert registry.get("mcp.tools.call") is None
