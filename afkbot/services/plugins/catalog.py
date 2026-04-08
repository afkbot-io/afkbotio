"""Known installable plugin catalog surfaced by the core CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KnownPlugin:
    """One catalog plugin that AFKBOT can suggest in the install wizard."""

    plugin_id: str
    name: str
    description: str
    source_ref: str


_KNOWN_PLUGINS: tuple[KnownPlugin, ...] = (
    KnownPlugin(
        plugin_id="afkbotui",
        name="AFKBOT UI",
        description="Unified AFKBOT web workspace for automations and future operator surfaces.",
        source_ref="github:afkbot-io/afkbotuiplugin@main",
    ),
)


def list_known_plugins() -> tuple[KnownPlugin, ...]:
    """Return the built-in installable plugin catalog in stable order."""

    return _KNOWN_PLUGINS


def list_uninstalled_known_plugins(*, installed_plugin_ids: set[str] | frozenset[str]) -> tuple[KnownPlugin, ...]:
    """Return catalog entries that are not currently installed."""

    return tuple(item for item in _KNOWN_PLUGINS if item.plugin_id not in installed_plugin_ids)


__all__ = ["KnownPlugin", "list_known_plugins", "list_uninstalled_known_plugins"]
