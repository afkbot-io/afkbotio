"""Typed error contracts for MCP integration services."""

from __future__ import annotations


class MCPIntegrationError(ValueError):
    """Base typed error for MCP integration flows."""


class MCPStorageError(MCPIntegrationError):
    """Raised when MCP config storage cannot be read or written safely."""
