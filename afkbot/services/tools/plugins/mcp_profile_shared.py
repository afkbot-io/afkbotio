"""Shared helpers for MCP profile-management tool plugins."""

from __future__ import annotations

from afkbot.services.mcp_integration.errors import MCPIntegrationError
from afkbot.services.profile_runtime.service import ProfileServiceError
from afkbot.services.tools.base import ToolResult


def mcp_tool_error(exc: Exception) -> ToolResult:
    """Convert one MCP/profile exception into a deterministic tool error result."""

    error_code = "mcp_error"
    if isinstance(exc, ProfileServiceError):
        error_code = "profile_not_found"
    elif isinstance(exc, ValueError):
        error_code = "mcp_invalid_input"
    elif isinstance(exc, MCPIntegrationError):
        normalized = str(exc).strip().lower()
        if normalized.startswith("mcp server not found"):
            error_code = "mcp_server_not_found"
    return ToolResult.error(error_code=error_code, reason=str(exc))
