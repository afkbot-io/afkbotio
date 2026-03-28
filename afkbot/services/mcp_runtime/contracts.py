"""Contracts for optional runtime MCP discovery hints."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MCPRuntimeToolDescriptor:
    """One sanitized remote MCP tool discovered for prompt hints and runtime listing."""

    runtime_name: str
    server_name: str
    remote_tool_name: str
    transport: str
    url: str
    description: str
    input_schema: dict[str, object]
