"""Parse-only URL helpers for remote MCP server drafts."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, Field

from afkbot.services.mcp_integration.contracts import MCPServerConfig
from afkbot.services.mcp_integration.validator import validate_server_config

_GENERIC_SEGMENTS = {"api", "app", "com", "dev", "http", "https", "io", "local", "mcp", "net", "org", "sse", "www"}
_VERSION_SEGMENT_RE = re.compile(r"^v\d+$", re.IGNORECASE)


class MCPURLResolution(BaseModel):
    """URL-derived suggestions used by the MCP add wizard and CLI defaults."""

    url: str = Field(min_length=1)
    suggested_server: str = Field(min_length=1)
    suggested_transport: str = Field(min_length=1)
    suggested_env_ref: str
    suggested_secret_ref: str


def resolve_mcp_url(url: str) -> MCPURLResolution:
    """Parse one remote MCP endpoint URL into deterministic suggestions."""

    normalized_url = _normalize_remote_url(url)
    parsed = urlparse(normalized_url)
    suggested_server = _infer_server_name(parsed)
    suggested_transport = _infer_transport(parsed)
    uppercase_server = suggested_server.upper().replace("-", "_")
    return MCPURLResolution(
        url=normalized_url,
        suggested_server=suggested_server,
        suggested_transport=suggested_transport,
        suggested_env_ref=f"MCP_{uppercase_server}_BASE_URL",
        suggested_secret_ref=f"mcp_{suggested_server}_token",
    )


def build_server_config_from_url(
    *,
    url: str,
    server: str,
    transport: str,
    capabilities: tuple[str, ...],
    env_refs: tuple[str, ...],
    secret_refs: tuple[str, ...],
    enabled: bool,
) -> MCPServerConfig:
    """Build one validated remote MCP config from resolved operator inputs."""

    normalized_url = _normalize_remote_url(url)
    normalized_transport = transport.strip().lower()
    if normalized_transport == "stdio":
        raise ValueError("stdio transport cannot be configured from a remote URL")
    payload = {
        "server": server,
        "transport": normalized_transport,
        "url": normalized_url,
        "capabilities": list(capabilities) or ["tools"],
        "env_refs": [{"env_ref": item} for item in env_refs],
        "secret_refs": [{"secret_ref": item} for item in secret_refs],
        "enabled": enabled,
    }
    return validate_server_config(payload)


def _normalize_remote_url(url: str) -> str:
    normalized = url.strip()
    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https", "ws", "wss"}:
        raise ValueError("MCP URL scheme must be one of: http, https, ws, wss")
    if not parsed.netloc:
        raise ValueError("MCP URL must include a host")
    return normalized


def _infer_transport(parsed: object) -> str:
    if not hasattr(parsed, "scheme") or not hasattr(parsed, "path") or not hasattr(parsed, "query"):
        raise AssertionError("parsed URL is missing required attributes")
    scheme = str(getattr(parsed, "scheme")).lower()
    if scheme in {"ws", "wss"}:
        return "websocket"
    path = str(getattr(parsed, "path")).lower()
    query = parse_qs(str(getattr(parsed, "query")).lower())
    if path.endswith("/sse") or "/sse/" in path or query.get("transport") == ["sse"]:
        return "sse"
    return "http"


def _infer_server_name(parsed: object) -> str:
    if not hasattr(parsed, "hostname") or not hasattr(parsed, "path"):
        raise AssertionError("parsed URL is missing required attributes")
    path_candidate = _pick_path_candidate(str(getattr(parsed, "path")))
    if path_candidate:
        return path_candidate
    hostname = str(getattr(parsed, "hostname") or "").strip().lower()
    if not hostname:
        return "remote-mcp"
    host_labels = [label for label in hostname.split(".") if label]
    for label in reversed(host_labels):
        if label in _GENERIC_SEGMENTS or _VERSION_SEGMENT_RE.fullmatch(label):
            continue
        return _slugify(label)
    return _slugify(host_labels[0]) if host_labels else "remote-mcp"


def _pick_path_candidate(path: str) -> str | None:
    segments = [segment for segment in path.split("/") if segment]
    for segment in reversed(segments):
        lowered = segment.strip().lower()
        if not lowered or lowered in _GENERIC_SEGMENTS or _VERSION_SEGMENT_RE.fullmatch(lowered):
            continue
        return _slugify(lowered)
    return None


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "remote-mcp"
