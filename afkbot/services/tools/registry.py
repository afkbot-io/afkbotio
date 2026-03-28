"""Registry for loading and resolving tool plugins."""

from __future__ import annotations

from collections.abc import Iterable
import logging

from afkbot.services.mcp_runtime.tool_bridge import build_mcp_runtime_tools
from afkbot.services.tools.base import ToolBase
from afkbot.services.tools.plugins import create_tool_from_plugin
from afkbot.settings import Settings

_LOGGER = logging.getLogger(__name__)


class ToolRegistry:
    """In-memory catalog of enabled tools keyed by canonical tool name."""

    def __init__(self, tools: Iterable[ToolBase] | None = None) -> None:
        self._tools: dict[str, ToolBase] = {}
        if tools is not None:
            for tool in tools:
                self.register(tool)

    @classmethod
    def from_plugins(
        cls,
        plugin_names: Iterable[str],
        *,
        settings: Settings,
    ) -> ToolRegistry:
        """Build registry from configured plugin identifiers."""

        registry = cls()
        for plugin_name in plugin_names:
            registry.register(create_tool_from_plugin(plugin_name, settings))
        return registry

    @classmethod
    def from_settings(cls, settings: Settings) -> ToolRegistry:
        """Build registry using `enabled_tool_plugins` from application settings."""

        return cls.from_plugins(settings.enabled_tool_plugins, settings=settings)

    @classmethod
    def from_profile_settings(
        cls,
        settings: Settings,
        *,
        profile_id: str,
    ) -> ToolRegistry:
        """Build registry for one profile, including optional profile-scoped runtime tools."""

        registry = cls.from_settings(settings)
        try:
            for tool in build_mcp_runtime_tools(settings=settings, profile_id=profile_id):
                registry.register(tool)
        except Exception as exc:
            _LOGGER.warning(
                "skipped runtime MCP tool registration for profile %s: %s",
                profile_id,
                exc,
            )
        return registry

    def register(self, tool: ToolBase) -> None:
        """Register one tool plugin by canonical unique name."""

        if tool.name in self._tools:
            raise ValueError(f"Tool is already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolBase | None:
        """Return tool by canonical name, or `None` when not found."""

        return self._tools.get(name)

    def list_names(self) -> tuple[str, ...]:
        """Return sorted canonical tool names."""

        return tuple(sorted(self._tools.keys()))
