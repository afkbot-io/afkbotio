"""Tests for runtime MCP catalog helpers."""

from __future__ import annotations

from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_runtime.client import normalize_mcp_schema
from afkbot.services.mcp_runtime.catalog import runtime_available_for_server
from afkbot.settings import Settings


def test_runtime_available_for_server_requires_enabled_remote_tool_server() -> None:
    """Runtime availability should require enabled remote servers with tool capability."""

    # Arrange
    settings = Settings(root_dir=".")
    config = MCPServerConfig.model_validate(
        {
            "server": "github",
            "transport": "http",
            "url": "https://example.com/mcp",
            "capabilities": ["tools"],
            "env_refs": [],
            "secret_refs": [{"secret_ref": "github_token"}],
            "enabled": True,
        }
    )

    # Act
    result = runtime_available_for_server(settings=settings, config=config)

    # Assert
    assert result is True


def test_runtime_available_for_server_rejects_stdio_and_disabled_configs() -> None:
    """Runtime availability should reject local-only and disabled MCP configs."""

    # Arrange
    settings = Settings(root_dir=".")
    disabled = MCPServerConfig.model_validate(
        {
            "server": "github",
            "transport": "http",
            "url": "https://example.com/mcp",
            "capabilities": ["tools"],
            "env_refs": [],
            "secret_refs": [{"secret_ref": "github_token"}],
            "enabled": False,
        }
    )
    stdio = MCPServerConfig.model_validate(
        {
            "server": "github",
            "transport": "stdio",
            "capabilities": ["tools"],
            "env_refs": [],
            "secret_refs": [{"secret_ref": "github_token"}],
            "enabled": True,
        }
    )

    # Act
    disabled_result = runtime_available_for_server(settings=settings, config=disabled)
    stdio_result = runtime_available_for_server(settings=settings, config=stdio)

    # Assert
    assert disabled_result is False
    assert stdio_result is False


def test_mcp_tool_schema_normalization_keeps_object_shape() -> None:
    """Runtime catalog should preserve MCP tool object schemas for later bridge validation."""

    # Arrange
    raw_schema: object = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    # Act
    schema = normalize_mcp_schema(raw_schema)

    # Assert
    assert schema["type"] == "object"
    assert schema["properties"]["query"]["type"] == "string"
