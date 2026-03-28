"""Validation helpers for MCP profile configuration payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from afkbot.services.mcp_integration.contracts import MCPServerConfig, REQUIRED_SERVER_FIELDS
from afkbot.services.mcp_integration.errors import MCPIntegrationError

_SENSITIVE_KEY_PARTS = ("secret", "token", "password", "api_key", "authorization")


class MCPConfigValidationError(MCPIntegrationError):
    """Raised when MCP profile payload does not satisfy strict contracts."""


def validate_server_config(
    payload: Mapping[str, Any],
    *,
    source: str | Path = "<memory>",
) -> MCPServerConfig:
    """Validate one server record with strict required fields and secure refs only."""

    _ensure_required_fields(payload, source=source)
    _ensure_no_plaintext_secrets(payload, source=source, path=())
    try:
        return MCPServerConfig.model_validate(payload)
    except ValidationError as exc:  # pragma: no cover - deterministic conversion
        raise MCPConfigValidationError(f"Invalid MCP server config in {source}: {exc}") from exc


def validate_server_configs(
    payloads: Sequence[Mapping[str, Any]],
    *,
    source: str | Path = "<memory>",
) -> list[MCPServerConfig]:
    """Validate many server records preserving source order."""

    validated: list[MCPServerConfig] = []
    for index, payload in enumerate(payloads):
        validated.append(validate_server_config(payload, source=f"{source}#{index}"))
    return validated


def _ensure_required_fields(payload: Mapping[str, Any], *, source: str | Path) -> None:
    keys = {str(key) for key in payload.keys()}
    missing = sorted(REQUIRED_SERVER_FIELDS.difference(keys))
    if missing:
        raise MCPConfigValidationError(
            f"Missing required MCP fields in {source}: {', '.join(missing)}"
        )


def _ensure_no_plaintext_secrets(
    payload: object,
    *,
    source: str | Path,
    path: tuple[str, ...],
) -> None:
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            key_name = str(key)
            next_path = (*path, key_name)

            if key_name == "env_refs":
                _ensure_secure_ref_list(
                    value=value,
                    source=source,
                    path=next_path,
                    allowed_key="env_ref",
                )
                continue
            if key_name == "secret_refs":
                _ensure_secure_ref_list(
                    value=value,
                    source=source,
                    path=next_path,
                    allowed_key="secret_ref",
                )
                continue

            if key_name not in {"env_ref", "secret_ref"} and _looks_sensitive_key(key_name):
                dotted = ".".join(next_path)
                raise MCPConfigValidationError(
                    f"Plaintext secret field is forbidden in {source}: {dotted}"
                )

            _ensure_no_plaintext_secrets(value, source=source, path=next_path)
        return

    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for index, item in enumerate(payload):
            _ensure_no_plaintext_secrets(item, source=source, path=(*path, str(index)))


def _ensure_secure_ref_list(
    *,
    value: object,
    source: str | Path,
    path: tuple[str, ...],
    allowed_key: str,
) -> None:
    dotted = ".".join(path)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise MCPConfigValidationError(f"{dotted} must be a list of {allowed_key} objects in {source}")

    for index, item in enumerate(value):
        item_path = f"{dotted}[{index}]"
        if not isinstance(item, Mapping):
            raise MCPConfigValidationError(
                f"{item_path} must be an object with only `{allowed_key}` in {source}"
            )
        keys = {str(key) for key in item.keys()}
        if keys != {allowed_key}:
            raise MCPConfigValidationError(
                f"{item_path} must contain only `{allowed_key}` in {source}"
            )
        raw_value = item.get(allowed_key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise MCPConfigValidationError(
                f"{item_path}.{allowed_key} must be a non-empty string in {source}"
            )


def _looks_sensitive_key(name: str) -> bool:
    lowered = name.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)
