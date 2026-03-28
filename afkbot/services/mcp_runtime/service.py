"""Runtime MCP orchestration service for profile-scoped list/call bridges."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_integration.profile_loader import MCPProfileLoader
from afkbot.services.mcp_runtime.catalog import MCPRuntimeCatalog, get_mcp_runtime_catalog
from afkbot.services.mcp_runtime.client import call_remote_mcp_tool
from afkbot.services.mcp_runtime.contracts import MCPRuntimeToolDescriptor
from afkbot.settings import Settings

_SUPPORTED_RUNTIME_TRANSPORTS = frozenset({"http", "sse", "websocket"})


class MCPRuntimeService:
    """Resolve runtime-eligible MCP servers and proxy discovery/calls."""

    def __init__(
        self,
        *,
        settings: Settings,
        profile_loader: MCPProfileLoader | None = None,
        catalog: MCPRuntimeCatalog | None = None,
    ) -> None:
        self._settings = settings
        self._profile_loader = profile_loader or MCPProfileLoader(settings)
        self._catalog = catalog or get_mcp_runtime_catalog(settings)

    def has_runtime_servers(self, *, profile_id: str) -> bool:
        """Return whether one profile currently has at least one runtime-eligible MCP server."""

        if not self._settings.mcp_runtime_enabled:
            return False
        return bool(self._runtime_configs(profile_id=profile_id))

    async def list_tools(
        self,
        *,
        profile_id: str,
        server: str | None = None,
        timeout_sec: int,
    ) -> tuple[MCPRuntimeToolDescriptor, ...]:
        """Return cached-or-live runtime MCP tool descriptors for one profile."""

        if not self._settings.mcp_runtime_enabled:
            return ()
        descriptors = await self._catalog.list_tools(
            profile_id=profile_id,
            timeout_sec=timeout_sec,
        )
        normalized_server = str(server or "").strip().lower()
        if not normalized_server:
            return descriptors
        return tuple(item for item in descriptors if item.server_name == normalized_server)

    async def call_tool(
        self,
        *,
        profile_id: str,
        server: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_sec: int,
    ) -> dict[str, object]:
        """Call one live runtime MCP tool and return a JSON-safe payload."""

        if not self._settings.mcp_runtime_enabled:
            raise ValueError("Runtime MCP bridge is disabled")
        config = self._require_runtime_config(profile_id=profile_id, server=server)
        response = await call_remote_mcp_tool(
            config=config,
            tool_name=tool_name,
            arguments=arguments or None,
            timeout_sec=timeout_sec,
        )
        serialized_content = _serialize_mcp_content(response.content)
        text_output = "\n".join(
            str(item["text"])
            for item in serialized_content
            if item.get("type") == "text" and isinstance(item.get("text"), str)
        ).strip()
        return {
            "server": config.server,
            "tool_name": tool_name,
            "content": serialized_content,
            "structured_content": (
                {}
                if response.structuredContent is None
                else _json_safe_mapping(dict(response.structuredContent))
            ),
            "is_error": bool(response.isError),
            "text": text_output,
        }

    def resolve_policy_servers(
        self,
        *,
        profile_id: str,
        server: str | None = None,
    ) -> tuple[MCPServerConfig, ...]:
        """Return runtime configs used to augment policy-visible endpoint params."""

        if not self._settings.mcp_runtime_enabled:
            return ()
        return self._select_runtime_configs(profile_id=profile_id, server=server)

    def _require_runtime_config(self, *, profile_id: str, server: str) -> MCPServerConfig:
        matches = self._select_runtime_configs(profile_id=profile_id, server=server)
        if not matches:
            raise ValueError(f"Runtime MCP server not found: {server.strip().lower()}")
        return matches[0]

    def _select_runtime_configs(
        self,
        *,
        profile_id: str,
        server: str | None,
    ) -> tuple[MCPServerConfig, ...]:
        normalized_server = str(server or "").strip().lower()
        configs = self._runtime_configs(profile_id=profile_id)
        if not normalized_server:
            return configs
        return tuple(config for config in configs if config.server == normalized_server)

    def _runtime_configs(self, *, profile_id: str) -> tuple[MCPServerConfig, ...]:
        configs = self._profile_loader.load_profile(profile_id)
        return tuple(config for config in configs if _is_runtime_eligible(config))


def _is_runtime_eligible(config: MCPServerConfig) -> bool:
    return bool(
        config.enabled
        and config.url
        and config.transport in _SUPPORTED_RUNTIME_TRANSPORTS
        and "tools" in config.capabilities
    )


def _serialize_mcp_content(content: object) -> list[dict[str, object]]:
    if not isinstance(content, list):
        return []
    serialized: list[dict[str, object]] = []
    for item in content:
        if hasattr(item, "model_dump"):
            dumped = item.model_dump(mode="json", by_alias=True, exclude_none=True)
            if isinstance(dumped, dict):
                serialized.append(_json_safe_mapping(dumped))
                continue
        serialized.append({"type": "unknown", "value": str(item)})
    return serialized


def _json_safe_mapping(payload: Mapping[str, object] | Mapping[object, object]) -> dict[str, object]:
    return {
        str(key): _to_jsonable(value) for key, value in payload.items()
    }


def _to_jsonable(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return _json_safe_mapping(value)
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return str(value)
