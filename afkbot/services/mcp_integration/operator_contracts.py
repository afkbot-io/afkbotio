"""Operator-facing MCP view models used by CLI surfaces."""

from __future__ import annotations

from pydantic import BaseModel, Field

from afkbot.services.mcp_integration.profile_store import MCPStorageMode

MCP_CONFIG_BOUNDARY_NOTE = (
    "Profile MCP configuration uses `afk mcp` or `mcp.profile.*`. Runtime MCP tool access uses "
    "`mcp.tools.list` / `mcp.tools.call` for enabled remote servers with `tools` capability and "
    "matching policy/network access."
)


class MCPServerAccessView(BaseModel):
    """Operator-facing access summary for one configured MCP server."""

    capabilities: tuple[str, ...] = ()
    env_refs: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()
    ide_visible: bool = True
    runtime_available: bool = False
    boundary_note: str = MCP_CONFIG_BOUNDARY_NOTE


class MCPServerView(BaseModel):
    """Merged effective MCP server config with source metadata."""

    server: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    url: str | None = None
    enabled: bool
    capabilities: tuple[str, ...] = ()
    env_refs: tuple[str, ...] = ()
    secret_refs: tuple[str, ...] = ()
    config_source: str | None = None
    access: MCPServerAccessView


class MCPValidationReport(BaseModel):
    """Structured profile-level MCP validation result."""

    ok: bool
    profile_id: str
    storage_mode: MCPStorageMode
    files_checked: tuple[str, ...] = ()
    servers: tuple[MCPServerView, ...] = ()
    notes: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class MCPAddPreview(BaseModel):
    """Preview of one pending operator-driven MCP add/update."""

    profile_id: str
    storage_mode: MCPStorageMode
    target_path: str
    would_replace_effective_server: bool
    server: MCPServerView


class MCPAddResult(BaseModel):
    """Persist result for one MCP add/update command."""

    profile_id: str
    storage_mode: MCPStorageMode
    target_path: str
    created: bool
    server: MCPServerView


class MCPRemovePreview(BaseModel):
    """Preview of one pending operator-driven MCP removal."""

    profile_id: str
    storage_mode: MCPStorageMode
    target_path: str
    server: MCPServerView


class MCPRemoveResult(BaseModel):
    """Persist result for one MCP remove command."""

    profile_id: str
    storage_mode: MCPStorageMode
    target_path: str
    removed_server: str = Field(min_length=1)
