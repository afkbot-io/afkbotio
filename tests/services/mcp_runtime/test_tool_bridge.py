"""Tests for the runtime MCP tool bridge."""

from __future__ import annotations

import pytest

from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_runtime.contracts import MCPRuntimeToolDescriptor
from afkbot.services.mcp_runtime.tool_bridge import (
    MCPToolsCallTool,
    MCPToolsListTool,
    _validate_mcp_arguments,
)
from afkbot.services.tools.base import ToolContext


class _FakeRuntimeService:
    def __init__(
        self,
        *,
        descriptors: tuple[MCPRuntimeToolDescriptor, ...],
        configs: tuple[MCPServerConfig, ...],
        call_payload: dict[str, object] | None = None,
    ) -> None:
        self._descriptors = descriptors
        self._configs = configs
        self._call_payload = {
            "server": "github",
            "tool_name": "search_issues",
            "content": [{"type": "text", "text": "found bug"}],
            "structured_content": {"count": 1},
            "is_error": False,
            "text": "found bug",
        } if call_payload is None else call_payload

    async def list_tools(
        self,
        *,
        profile_id: str,
        server: str | None = None,
        timeout_sec: int,
    ) -> tuple[MCPRuntimeToolDescriptor, ...]:
        _ = profile_id, timeout_sec
        if server is None:
            return self._descriptors
        return tuple(item for item in self._descriptors if item.server_name == server)

    async def call_tool(
        self,
        *,
        profile_id: str,
        server: str,
        tool_name: str,
        arguments: dict[str, object],
        timeout_sec: int,
    ) -> dict[str, object]:
        _ = profile_id, timeout_sec
        assert server == "github"
        assert tool_name == "search_issues"
        assert arguments == {"query": "bug"}
        return self._call_payload

    def resolve_policy_servers(
        self,
        *,
        profile_id: str,
        server: str | None = None,
    ) -> tuple[MCPServerConfig, ...]:
        _ = profile_id
        if server is None:
            return self._configs
        return tuple(item for item in self._configs if item.server == server)


@pytest.fixture()
def runtime_descriptor() -> MCPRuntimeToolDescriptor:
    """Provide one sanitized runtime MCP tool descriptor."""

    return MCPRuntimeToolDescriptor(
        runtime_name="mcp.github.search_issues",
        server_name="github",
        remote_tool_name="search_issues",
        transport="http",
        url="https://example.com/mcp",
        description="Search GitHub issues",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )


@pytest.fixture()
def runtime_config() -> MCPServerConfig:
    """Provide one runtime-eligible MCP server config."""

    return MCPServerConfig.model_validate(
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


def test_validate_mcp_arguments_reports_schema_error(
    runtime_descriptor: MCPRuntimeToolDescriptor,
) -> None:
    """Runtime MCP calls should fail fast when remote JSON-schema validation fails."""

    # Arrange
    arguments: dict[str, object] = {}

    # Act
    result = _validate_mcp_arguments(
        tool_name=runtime_descriptor.remote_tool_name,
        arguments=arguments,
        input_schema=runtime_descriptor.input_schema,
    )

    # Assert
    assert result is not None
    assert result.ok is False
    assert result.error_code == "tool_params_invalid"
    assert "'query' is a required property" in (result.reason or "")


@pytest.mark.asyncio
async def test_mcp_tools_list_returns_discovered_remote_tools(
    runtime_descriptor: MCPRuntimeToolDescriptor,
    runtime_config: MCPServerConfig,
) -> None:
    """The generic list bridge should expose live runtime MCP descriptors."""

    # Arrange
    tool = MCPToolsListTool(
        service=_FakeRuntimeService(
            descriptors=(runtime_descriptor,),
            configs=(runtime_config,),
        ),
        profile_id="default",
    )
    params = tool.parse_params(
        {"server": "github"},
        default_timeout_sec=15,
        max_timeout_sec=60,
    )

    # Act
    result = await tool.execute(
        ToolContext(profile_id="default", session_id="s-1", run_id=1),
        params,
    )

    # Assert
    assert result.ok is True
    assert result.payload["servers"] == ["github"]
    assert result.payload["tools"] == [
        {
            "server": "github",
            "transport": "http",
            "runtime_name": "mcp.github.search_issues",
            "tool_name": "search_issues",
            "description": "Search GitHub issues",
            "input_schema": runtime_descriptor.input_schema,
        }
    ]


@pytest.mark.asyncio
async def test_mcp_tools_call_executes_remote_tool_with_nested_arguments(
    runtime_descriptor: MCPRuntimeToolDescriptor,
    runtime_config: MCPServerConfig,
) -> None:
    """The generic call bridge should proxy one remote MCP tool execution."""

    # Arrange
    tool = MCPToolsCallTool(
        service=_FakeRuntimeService(
            descriptors=(runtime_descriptor,),
            configs=(runtime_config,),
        ),
        profile_id="default",
    )
    params = tool.parse_params(
        {
            "server": "github",
            "tool_name": "search_issues",
            "arguments": {"query": "bug"},
        },
        default_timeout_sec=15,
        max_timeout_sec=60,
    )

    # Act
    result = await tool.execute(
        ToolContext(profile_id="default", session_id="s-1", run_id=1),
        params,
    )

    # Assert
    assert result.ok is True
    assert result.payload["server"] == "github"
    assert result.payload["tool_name"] == "search_issues"
    assert result.payload["text"] == "found bug"
    assert result.payload["structured_content"] == {"count": 1}


def test_mcp_tools_call_policy_params_include_hidden_endpoint(
    runtime_config: MCPServerConfig,
) -> None:
    """Runtime MCP calls should expose only policy-relevant MCP endpoint details."""

    # Arrange
    tool = MCPToolsCallTool(
        service=_FakeRuntimeService(descriptors=(), configs=(runtime_config,)),
        profile_id="default",
    )

    # Act
    params = tool.policy_params({"server": "github", "tool_name": "search_issues"})

    # Assert
    assert params["server"] == "github"
    assert params["tool_name"] == "search_issues"
    assert params["endpoint_url"] == "https://example.com/mcp"
