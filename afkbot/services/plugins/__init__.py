"""Installable plugin runtime services."""

from afkbot.services.plugins.catalog import KnownPlugin, list_known_plugins, list_uninstalled_known_plugins
from afkbot.services.plugins.contracts import PluginConfigMetadata, PluginServiceError
from afkbot.services.plugins.scaffold import PluginScaffoldResult, scaffold_plugin
from afkbot.services.plugins.service import PluginService, get_plugin_service

__all__ = [
    "KnownPlugin",
    "PluginConfigMetadata",
    "PluginScaffoldResult",
    "PluginService",
    "PluginServiceError",
    "get_plugin_service",
    "list_known_plugins",
    "list_uninstalled_known_plugins",
    "scaffold_plugin",
]
