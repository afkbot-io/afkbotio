"""Boundary tests between MCP integration module and runtime ToolRegistry."""

from __future__ import annotations

import json
from pathlib import Path

from afkbot.services.mcp_integration.profile_loader import MCPProfileLoader
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings


def test_mcp_integration_configs_do_not_create_runtime_mcp_tools_in_static_registry(
    tmp_path: Path,
) -> None:
    """Loading MCP profile config must not affect the static plugin-only tool registry."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "mcp.json").write_text(
        json.dumps(
            [
                {
                    "server": "github",
                    "transport": "stdio",
                    "capabilities": ["tools"],
                    "env_refs": [{"env_ref": "GITHUB_URL"}],
                    "secret_refs": [{"secret_ref": "github_token"}],
                    "enabled": True,
                }
            ]
        ),
        encoding="utf-8",
    )

    settings = Settings(root_dir=tmp_path)
    loader = MCPProfileLoader(settings)
    registry = ToolRegistry.from_settings(settings)

    # Act
    payload = loader.load_profile_for_ide("default")

    # Assert
    assert payload["servers"]
    assert all(not name.startswith("mcp.") for name in registry.list_names())
    assert registry.get("mcp.github") is None


def test_profile_registry_adds_runtime_mcp_bridge_for_remote_tool_servers(tmp_path: Path) -> None:
    """Profile-aware registry should include the generic runtime MCP bridge."""

    # Arrange
    profile_root = tmp_path / "profiles/default"
    profile_root.mkdir(parents=True)
    (profile_root / "mcp.json").write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server": "github",
                        "transport": "http",
                        "url": "https://example.com/mcp",
                        "capabilities": ["tools"],
                        "env_refs": [],
                        "secret_refs": [{"secret_ref": "github_token"}],
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
    assert "mcp.tools.list" in registry.list_names()
    assert "mcp.tools.call" in registry.list_names()
    assert registry.get("mcp.tools.list") is not None
    assert registry.get("mcp.tools.call") is not None
