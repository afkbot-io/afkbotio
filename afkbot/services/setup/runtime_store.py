"""Persistent runtime config/secrets store independent from .env files."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from afkbot.services.atomic_writes import atomic_json_write
from afkbot.settings import Settings


RUNTIME_STORE_VERSION = 1
_SECRETS_ENCRYPTION_SCHEME = "fernet-v1"
_SECRETS_ENV_KEY = "AFKBOT_RUNTIME_SECRETS_KEY"


def read_runtime_config(settings: Settings) -> dict[str, Any]:
    """Read persisted runtime config payload."""

    payload = _read_json_object(settings.runtime_config_path)
    if payload.get("version") != RUNTIME_STORE_VERSION:
        return {}
    config = payload.get("config")
    if isinstance(config, dict):
        return config
    return {}


def read_runtime_secrets(settings: Settings) -> dict[str, str]:
    """Read persisted runtime secrets payload."""

    payload = _read_json_object(settings.runtime_secrets_path)
    if payload.get("version") != RUNTIME_STORE_VERSION:
        return {}
    legacy_secrets = payload.get("secrets")
    if isinstance(legacy_secrets, dict):
        return _normalize_secrets_dict(legacy_secrets)

    encryption = str(payload.get("encryption") or "").strip().lower()
    ciphertext = payload.get("ciphertext")
    if encryption != _SECRETS_ENCRYPTION_SCHEME or not isinstance(ciphertext, str) or not ciphertext:
        return {}

    key = _resolve_runtime_secrets_key(settings=settings, create_if_missing=False)
    if key is None:
        return {}
    try:
        raw = Fernet(key).decrypt(ciphertext.encode("utf-8"))
        decoded = json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(decoded, dict):
        return {}
    return _normalize_secrets_dict(decoded)


def write_runtime_config(settings: Settings, *, config: dict[str, Any]) -> None:
    """Persist runtime config payload."""

    payload = {
        "version": RUNTIME_STORE_VERSION,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "config": config,
    }
    atomic_json_write(settings.runtime_config_path, payload, mode=0o600)


def write_runtime_secrets(settings: Settings, *, secrets: dict[str, str]) -> None:
    """Persist encrypted runtime secrets payload with strict file permissions."""

    normalized = _normalize_secrets_dict(secrets)
    key = _resolve_runtime_secrets_key(settings=settings, create_if_missing=True)
    if key is None:
        raise ValueError("Runtime secrets key is unavailable")
    plaintext = json.dumps(normalized, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    ciphertext = Fernet(key).encrypt(plaintext).decode("utf-8")
    payload = {
        "version": RUNTIME_STORE_VERSION,
        "updated_at": datetime.now(tz=UTC).isoformat(),
        "encryption": _SECRETS_ENCRYPTION_SCHEME,
        "ciphertext": ciphertext,
    }
    atomic_json_write(settings.runtime_secrets_path, payload, mode=0o600)


def clear_runtime_store(settings: Settings) -> None:
    """Delete persisted runtime config/secrets files."""

    for path in (
        settings.runtime_config_path,
        settings.runtime_secrets_path,
        settings.runtime_secrets_key_path,
    ):
        if path.exists():
            path.unlink()


def _normalize_secrets_dict(secrets: Mapping[str, object]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in secrets.items():
        if isinstance(value, str):
            normalized[key] = value
    return normalized


def _resolve_runtime_secrets_key(*, settings: Settings, create_if_missing: bool) -> bytes | None:
    env_value = str(os.getenv(_SECRETS_ENV_KEY) or "").strip()
    if env_value:
        encoded = env_value.encode("utf-8")
        try:
            _ = Fernet(encoded)
        except ValueError as exc:
            raise ValueError(f"{_SECRETS_ENV_KEY} is not a valid Fernet key") from exc
        return encoded

    key_path = settings.runtime_secrets_key_path
    if key_path.exists():
        raw = key_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        encoded = raw.encode("utf-8")
        try:
            _ = Fernet(encoded)
        except ValueError:
            return None
        return encoded

    if not create_if_missing:
        return None
    generated = Fernet.generate_key()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(generated.decode("utf-8"), encoding="utf-8")
    os.chmod(key_path, 0o600)
    return generated


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload

