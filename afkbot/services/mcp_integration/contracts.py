"""Contracts for profile-scoped MCP IDE integration."""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

REQUIRED_SERVER_FIELDS: Final[frozenset[str]] = frozenset(
    {"server", "transport", "capabilities", "env_refs", "secret_refs", "enabled"}
)
ALLOWED_TRANSPORTS: Final[frozenset[str]] = frozenset({"stdio", "http", "sse", "websocket"})
ALLOWED_CAPABILITIES: Final[frozenset[str]] = frozenset({"tools", "resources", "prompts"})

_SERVER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)
_REF_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")


class MCPEnvRef(BaseModel):
    """Reference to one environment variable."""

    model_config = ConfigDict(extra="forbid")

    env_ref: str = Field(min_length=1)

    @field_validator("env_ref")
    @classmethod
    def _validate_env_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not _REF_NAME_RE.fullmatch(normalized):
            raise ValueError("Invalid env_ref format")
        return normalized


class MCPSecretRef(BaseModel):
    """Reference to one secret name in external vault/secret store."""

    model_config = ConfigDict(extra="forbid")

    secret_ref: str = Field(min_length=1)

    @field_validator("secret_ref")
    @classmethod
    def _validate_secret_ref(cls, value: str) -> str:
        normalized = value.strip()
        if not _REF_NAME_RE.fullmatch(normalized):
            raise ValueError("Invalid secret_ref format")
        return normalized


class MCPServerConfig(BaseModel):
    """Normalized one-server MCP integration configuration."""

    model_config = ConfigDict(extra="forbid")

    server: str = Field(min_length=1)
    transport: str = Field(min_length=1)
    url: str | None = None
    capabilities: tuple[str, ...]
    env_refs: tuple[MCPEnvRef, ...]
    secret_refs: tuple[MCPSecretRef, ...]
    enabled: bool

    @field_validator("server")
    @classmethod
    def _validate_server(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SERVER_RE.fullmatch(normalized):
            raise ValueError("Invalid server identifier")
        return normalized

    @field_validator("transport")
    @classmethod
    def _validate_transport(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ALLOWED_TRANSPORTS:
            raise ValueError(f"Unsupported transport: {value}")
        return normalized

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        parsed = urlparse(normalized)
        if parsed.scheme.lower() not in {"http", "https", "ws", "wss"}:
            raise ValueError("Unsupported MCP URL scheme")
        if not parsed.netloc:
            raise ValueError("MCP URL must include a host")
        return normalized

    @field_validator("capabilities")
    @classmethod
    def _validate_capabilities(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("capabilities must not be empty")
        normalized: list[str] = []
        for item in value:
            capability = item.strip().lower()
            if capability not in ALLOWED_CAPABILITIES:
                raise ValueError(f"Unsupported capability: {item}")
            normalized.append(capability)
        # Preserve deterministic order while removing duplicates.
        return tuple(dict.fromkeys(normalized))

    @model_validator(mode="after")
    def _validate_transport_url_pair(self) -> "MCPServerConfig":
        if self.url is None:
            return self
        parsed = urlparse(self.url)
        scheme = parsed.scheme.lower()
        if self.transport == "stdio":
            raise ValueError("stdio transport must not declare a remote URL")
        if self.transport == "websocket" and scheme not in {"ws", "wss"}:
            raise ValueError("websocket transport requires a ws:// or wss:// URL")
        if self.transport in {"http", "sse"} and scheme not in {"http", "https"}:
            raise ValueError("http/sse transport requires an http:// or https:// URL")
        return self
