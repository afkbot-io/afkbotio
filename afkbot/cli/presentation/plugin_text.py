"""Human-readable rendering helpers for plugin CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from afkbot.services.plugins import PluginConfigMetadata, PluginScaffoldResult
from afkbot.services.plugins.contracts import InstalledPluginRecord


def format_plugin_list(items: tuple[InstalledPluginRecord, ...]) -> str:
    """Render a compact installed-plugin list for humans."""

    if not items:
        return "No plugins installed."
    lines = [f"Installed plugins: {len(items)}"]
    for item in items:
        lines.append(
            f"- {item.plugin_id}: {item.name} v{item.version}, enabled={item.enabled}, source={item.source_ref}"
        )
    return "\n".join(lines)


def format_plugin_record(
    item: InstalledPluginRecord,
    *,
    heading: str,
) -> str:
    """Render one installed plugin record for human inspection."""

    manifest = item.manifest
    capabilities = ", ".join(_enabled_names(manifest.capabilities.model_dump())) or "none"
    config_fields = ", ".join(sorted(manifest.config_schema.fields)) or "none"
    lines = [
        f"{heading}: {item.plugin_id}",
        f"- name: {item.name}",
        f"- version: {item.version}",
        f"- enabled: {item.enabled}",
        f"- source_kind: {item.source_kind}",
        f"- source_ref: {item.source_ref}",
        f"- install_path: {item.install_path}",
        f"- installed_at: {item.installed_at.isoformat()}",
        f"- description: {manifest.description or '-'}",
        f"- afkbot_version: {manifest.afkbot_version}",
        f"- entrypoint: {manifest.entrypoint}",
        f"- capabilities: {capabilities}",
        (
            "- permissions: "
            f"database={manifest.permissions.database}, "
            f"taskflow={manifest.permissions.taskflow}, "
            f"outbound_http={manifest.permissions.outbound_http}, "
            f"data_dir_write={manifest.permissions.data_dir_write}"
        ),
        f"- mounts.api_prefix: {manifest.mounts.api_prefix or '-'}",
        f"- mounts.web_prefix: {manifest.mounts.web_prefix or '-'}",
        f"- config_fields: {config_fields}",
    ]
    return "\n".join(lines)


def format_plugin_config(
    item: PluginConfigMetadata,
    *,
    heading: str,
) -> str:
    """Render one resolved plugin config payload for human inspection."""

    lines = [
        f"{heading}: {item.plugin_id}",
        f"- source: {item.source}",
        f"- config_path: {item.config_path}",
        f"- data_dir: {item.data_dir}",
        f"- schema_fields: {', '.join(sorted(item.config_schema.fields)) or 'none'}",
        "- config:",
    ]
    if not item.config:
        lines.append("  - (empty)")
    else:
        for key, value in item.config.items():
            lines.append(f"  - {key}: {_render_value(value)}")
    return "\n".join(lines)


def format_plugin_scaffold(result: PluginScaffoldResult) -> str:
    """Render scaffold creation paths for humans."""

    lines = [
        "Plugin scaffold created.",
        f"- plugin_root: {result.plugin_root}",
        f"- manifest_path: {result.manifest_path}",
        f"- entrypoint_path: {result.entrypoint_path}",
        f"- readme_path: {result.plugin_root / 'README.md'}",
    ]
    return "\n".join(lines)


def _enabled_names(flags: dict[str, bool]) -> tuple[str, ...]:
    return tuple(key for key, enabled in flags.items() if enabled)


def _render_value(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


__all__ = [
    "format_plugin_config",
    "format_plugin_list",
    "format_plugin_record",
    "format_plugin_scaffold",
]
