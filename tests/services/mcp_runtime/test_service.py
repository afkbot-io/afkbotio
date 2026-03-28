"""Tests for runtime MCP service orchestration."""

from __future__ import annotations

from afkbot.services.mcp_runtime.contracts import MCPRuntimeToolDescriptor
from afkbot.services.mcp_runtime.service import MCPRuntimeService
from afkbot.settings import Settings


class _FakeCatalog:
    def __init__(self, descriptors: tuple[MCPRuntimeToolDescriptor, ...]) -> None:
        self._descriptors = descriptors
        self.calls: list[tuple[str, int]] = []

    async def list_tools(
        self,
        *,
        profile_id: str,
        timeout_sec: int | None = None,
    ) -> tuple[MCPRuntimeToolDescriptor, ...]:
        self.calls.append((profile_id, int(timeout_sec or 0)))
        return self._descriptors


async def test_runtime_service_list_tools_forwards_timeout_and_filters_server() -> None:
    """Runtime MCP service should forward requested timeouts into catalog discovery and server filters."""

    # Arrange
    catalog = _FakeCatalog(
        (
            MCPRuntimeToolDescriptor(
                runtime_name="mcp.alpha.search",
                server_name="alpha",
                remote_tool_name="search",
                transport="http",
                url="https://alpha.example.com/mcp",
                description="Search",
                input_schema={"type": "object", "properties": {}, "additionalProperties": True},
            ),
            MCPRuntimeToolDescriptor(
                runtime_name="mcp.beta.list",
                server_name="beta",
                remote_tool_name="list",
                transport="http",
                url="https://beta.example.com/mcp",
                description="List",
                input_schema={"type": "object", "properties": {}, "additionalProperties": True},
            ),
        )
    )
    service = MCPRuntimeService(settings=Settings(root_dir="."), catalog=catalog)

    # Act
    descriptors = await service.list_tools(
        profile_id="default",
        server="alpha",
        timeout_sec=7,
    )

    # Assert
    assert catalog.calls == [("default", 7)]
    assert descriptors == (
        MCPRuntimeToolDescriptor(
            runtime_name="mcp.alpha.search",
            server_name="alpha",
            remote_tool_name="search",
            transport="http",
            url="https://alpha.example.com/mcp",
            description="Search",
            input_schema={"type": "object", "properties": {}, "additionalProperties": True},
        ),
    )
