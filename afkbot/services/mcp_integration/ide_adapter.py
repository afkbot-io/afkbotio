"""Serialization adapter for IDE-facing MCP payload."""

from __future__ import annotations

from collections.abc import Sequence

from afkbot.services.mcp_integration.contracts import MCPServerConfig


def serialize_profile_for_ide(configs: Sequence[MCPServerConfig]) -> dict[str, object]:
    """Convert validated server configs into deterministic IDE payload shape."""

    ordered = sorted(configs, key=lambda item: item.server)
    return {"servers": [serialize_server_for_ide(item) for item in ordered]}


def serialize_server_for_ide(config: MCPServerConfig) -> dict[str, object]:
    """Serialize one server config using plain JSON-compatible objects."""

    return {
        "server": config.server,
        "transport": config.transport,
        **({"url": config.url} if config.url else {}),
        "capabilities": list(config.capabilities),
        "env_refs": [{"env_ref": item.env_ref} for item in config.env_refs],
        "secret_refs": [{"secret_ref": item.secret_ref} for item in config.secret_refs],
        "enabled": config.enabled,
    }
