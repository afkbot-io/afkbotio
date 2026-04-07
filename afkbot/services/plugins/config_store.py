"""Shared config storage helpers for embedded AFKBOT plugins."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from afkbot.services.atomic_writes import atomic_json_write
from afkbot.services.plugins.contracts import PluginServiceError


def read_plugin_config(*, path: Path, default_config: dict[str, object]) -> tuple[dict[str, object], str]:
    """Read one plugin config JSON object or return its manifest defaults."""

    if not path.exists():
        return dict(default_config), "default"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PluginServiceError(
            error_code="plugin_config_invalid",
            reason=f"Plugin config is invalid JSON: {path}",
        ) from exc
    if not isinstance(payload, dict):
        raise PluginServiceError(
            error_code="plugin_config_invalid",
            reason=f"Plugin config must be a JSON object: {path}",
        )
    merged = dict(default_config)
    merged.update(cast(dict[str, object], payload))
    return merged, "persisted"


def write_plugin_config(*, path: Path, payload: dict[str, object]) -> None:
    """Write one plugin config JSON object atomically."""

    if not isinstance(payload, dict):
        raise PluginServiceError(
            error_code="plugin_config_invalid",
            reason="Plugin config payload must be a JSON object",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_write(path, payload, mode=0o600)


def reset_plugin_config(*, path: Path) -> None:
    """Delete one persisted plugin config file when present."""

    if path.exists():
        path.unlink()
