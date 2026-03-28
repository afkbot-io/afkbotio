"""Transport clients and schema helpers for runtime MCP access."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import anyio
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.websocket import websocket_client
from mcp.types import CallToolResult, ListToolsResult

from afkbot.services.mcp_integration.contracts import MCPServerConfig

_SUPPORTED_RUNTIME_TRANSPORTS = frozenset({"http", "sse", "websocket"})


def normalize_mcp_schema(raw_schema: object) -> dict[str, object]:
    """Return one JSON-safe object schema for MCP tool arguments."""

    if not isinstance(raw_schema, dict):
        return {"type": "object", "properties": {}, "additionalProperties": True}
    normalized = {
        str(key): _to_jsonable(value) for key, value in raw_schema.items()
    }
    if normalized.get("type") != "object":
        normalized["type"] = "object"
    properties = normalized.get("properties")
    normalized["properties"] = properties if isinstance(properties, dict) else {}
    required = normalized.get("required")
    if not isinstance(required, list):
        normalized["required"] = []
    else:
        normalized["required"] = [str(item) for item in required if str(item).strip()]
    normalized.setdefault("additionalProperties", True)
    return normalized


@asynccontextmanager
async def open_mcp_client_session(
    config: MCPServerConfig,
    *,
    timeout_sec: int,
) -> AsyncIterator[ClientSession]:
    """Open one initialized MCP client session for a runtime-eligible server."""

    if config.transport not in _SUPPORTED_RUNTIME_TRANSPORTS:
        raise ValueError(f"Unsupported runtime MCP transport: {config.transport}")
    if not config.url:
        raise ValueError(f"Runtime MCP server is missing URL: {config.server}")

    with anyio.fail_after(float(timeout_sec)):
        if config.transport == "http":
            async with streamablehttp_client(
                config.url,
                timeout=timeout_sec,
                sse_read_timeout=timeout_sec,
            ) as (read_stream, write_stream, _get_session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=timeout_sec),
                ) as session:
                    await session.initialize()
                    yield session
            return

        if config.transport == "sse":
            async with sse_client(
                config.url,
                timeout=float(timeout_sec),
                sse_read_timeout=float(timeout_sec),
            ) as (read_stream, write_stream):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=timeout_sec),
                ) as session:
                    await session.initialize()
                    yield session
            return

        async with websocket_client(config.url) as (read_stream, write_stream):
            async with ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=timeout_sec),
            ) as session:
                await session.initialize()
                yield session


async def list_remote_mcp_tools(
    *,
    config: MCPServerConfig,
    timeout_sec: int,
) -> ListToolsResult:
    """List live tools from one runtime-eligible MCP server."""

    async with open_mcp_client_session(config=config, timeout_sec=timeout_sec) as session:
        response = await session.list_tools()
    return response


async def call_remote_mcp_tool(
    *,
    config: MCPServerConfig,
    tool_name: str,
    arguments: dict[str, Any] | None,
    timeout_sec: int,
) -> CallToolResult:
    """Call one live remote MCP tool through an initialized client session."""

    async with open_mcp_client_session(config=config, timeout_sec=timeout_sec) as session:
        response = await session.call_tool(
            tool_name,
            arguments=arguments,
            read_timeout_seconds=timedelta(seconds=timeout_sec),
        )
    return response


def _to_jsonable(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return str(value)
