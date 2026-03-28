"""Shared MCP JSON payload parsing helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from afkbot.services.mcp_integration.errors import MCPStorageError
from afkbot.services.mcp_integration.validator import MCPConfigValidationError


def read_json_payload(path: Path) -> object:
    """Read one JSON payload from disk with deterministic error translation."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise MCPConfigValidationError(f"Invalid UTF-8 in {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MCPConfigValidationError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise MCPStorageError(f"Unable to read MCP config {path}: {exc}") from exc


def extract_server_payload_items(payload: object, *, source: Path) -> list[Mapping[str, Any]]:
    """Extract raw server objects from one MCP JSON payload."""

    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return [_as_mapping(item=item, source=source) for item in payload]

    if isinstance(payload, Mapping):
        if "servers" in payload:
            servers = payload["servers"]
            if not isinstance(servers, Sequence) or isinstance(servers, (str, bytes, bytearray)):
                raise MCPConfigValidationError(f"`servers` must be a list in {source}")
            return [_as_mapping(item=item, source=source) for item in servers]
        return [_as_mapping(item=payload, source=source)]

    raise MCPConfigValidationError(
        f"Invalid MCP config payload in {source}: expected object, list, or object with `servers`"
    )


def _as_mapping(item: object, *, source: Path) -> Mapping[str, Any]:
    if not isinstance(item, Mapping):
        raise MCPConfigValidationError(f"Expected object server config in {source}, got {type(item)!r}")
    return item
