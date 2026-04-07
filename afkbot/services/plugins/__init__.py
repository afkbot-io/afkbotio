"""Installable plugin runtime services."""

from afkbot.services.plugins.contracts import PluginConfigMetadata, PluginServiceError
from afkbot.services.plugins.scaffold import PluginScaffoldResult, scaffold_plugin
from afkbot.services.plugins.service import PluginService, get_plugin_service

__all__ = [
    "PluginConfigMetadata",
    "PluginScaffoldResult",
    "PluginService",
    "PluginServiceError",
    "get_plugin_service",
    "scaffold_plugin",
]
