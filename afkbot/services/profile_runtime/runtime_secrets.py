"""Encrypted profile-local runtime secrets store."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from afkbot.services.atomic_writes import atomic_json_write
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.profile_runtime.contracts import ProfileRuntimeSecretsView
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "ProfileRuntimeSecretsService"] = {}
PROFILE_RUNTIME_SECRETS_VERSION = 1
_PROFILE_RUNTIME_SECRETS_FILENAME = "agent_secrets.json"
_PROFILE_RUNTIME_SECRETS_KEY_FILENAME = "agent_secrets.key"
_PROFILE_RUNTIME_SECRET_FIELDS = frozenset(
    {
        "llm_api_key",
        "openrouter_api_key",
        "openai_api_key",
        "openai_codex_api_key",
        "claude_api_key",
        "moonshot_api_key",
        "deepseek_api_key",
        "xai_api_key",
        "qwen_api_key",
        "minimax_portal_api_key",
        "minimax_portal_refresh_token",
        "minimax_portal_token_expires_at",
        "minimax_portal_resource_url",
        "minimax_portal_region",
        "github_copilot_api_key",
        "custom_api_key",
        "brave_api_key",
    }
)


class ProfileRuntimeSecretsService:
    """Read, write, and merge encrypted runtime secrets scoped to one profile."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def secrets_path(self, profile_id: str) -> Path:
        """Return absolute encrypted secrets file path for one profile."""

        return self._safe_profile_root(profile_id) / ".system" / _PROFILE_RUNTIME_SECRETS_FILENAME

    def key_path(self, profile_id: str) -> Path:
        """Return absolute key file path for one profile-local secrets store."""

        return self._safe_profile_root(profile_id) / ".system" / _PROFILE_RUNTIME_SECRETS_KEY_FILENAME

    def load(self, profile_id: str) -> dict[str, str]:
        """Load decrypted profile-local runtime secrets."""

        payload = self._read_json_object(self.secrets_path(profile_id))
        if payload.get("version") != PROFILE_RUNTIME_SECRETS_VERSION:
            return {}
        encryption = str(payload.get("encryption") or "").strip().lower()
        ciphertext = payload.get("ciphertext")
        if encryption != "fernet-v1" or not isinstance(ciphertext, str) or not ciphertext:
            return {}
        key = self._resolve_key(profile_id=profile_id, create_if_missing=False)
        if key is None:
            return {}
        try:
            raw = Fernet(key).decrypt(ciphertext.encode("utf-8"))
            decoded = json.loads(raw.decode("utf-8"))
        except (InvalidToken, ValueError, json.JSONDecodeError):
            return {}
        if not isinstance(decoded, dict):
            return {}
        return self._normalize_secrets(decoded)

    def write(self, profile_id: str, secrets: Mapping[str, str]) -> Path | None:
        """Persist encrypted runtime secrets for one profile, or remove empty payloads."""

        normalized = self._normalize_secrets(secrets)
        if not normalized:
            self.remove(profile_id)
            return None
        from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service

        get_profile_runtime_config_service(self._settings).ensure_layout(profile_id)
        key = self._resolve_key(profile_id=profile_id, create_if_missing=True)
        if key is None:
            raise ValueError("Profile runtime secrets key is unavailable")
        plaintext = json.dumps(normalized, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        ciphertext = Fernet(key).encrypt(plaintext).decode("utf-8")
        payload = {
            "version": PROFILE_RUNTIME_SECRETS_VERSION,
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "encryption": "fernet-v1",
            "ciphertext": ciphertext,
        }
        path = self.secrets_path(profile_id)
        atomic_json_write(path, payload, mode=0o600)
        return path

    def merge(self, profile_id: str, secrets: Mapping[str, str]) -> dict[str, str]:
        """Merge provided secrets with current payload and persist the result."""

        merged = self.load(profile_id)
        merged.update(self._normalize_secrets(secrets))
        self.write(profile_id, merged)
        return self.load(profile_id)

    def clear(self, profile_id: str, *, fields: tuple[str, ...] | None = None) -> dict[str, str]:
        """Clear selected secret fields, or all profile-local runtime secrets."""

        if not fields:
            self.remove(profile_id)
            return {}
        normalized_fields = {
            field.strip()
            for field in fields
            if field.strip() in _PROFILE_RUNTIME_SECRET_FIELDS
        }
        if not normalized_fields:
            return self.load(profile_id)
        merged = self.load(profile_id)
        for field in normalized_fields:
            merged.pop(field, None)
        self.write(profile_id, merged)
        return self.load(profile_id)

    def remove(self, profile_id: str) -> None:
        """Delete profile-local encrypted secrets and key material."""

        for path in (self.secrets_path(profile_id), self.key_path(profile_id)):
            if path.exists():
                path.unlink()

    @staticmethod
    def apply_to_settings(*, settings: Settings, secrets: Mapping[str, str]) -> Settings:
        """Build validated settings with profile-local secret overrides applied."""

        merged = settings.model_dump()
        merged.update(ProfileRuntimeSecretsService._normalize_secrets(secrets))
        return Settings(**merged)

    def describe(self, profile_id: str) -> ProfileRuntimeSecretsView:
        """Return serializable status for local profile runtime secrets."""

        secrets = self.load(profile_id)
        fields = tuple(sorted(secrets.keys()))
        return ProfileRuntimeSecretsView(
            configured_fields=fields,
            has_profile_secrets=bool(fields),
        )

    def _safe_profile_root(self, profile_id: str) -> Path:
        validate_profile_id(profile_id)
        profiles_root = self._settings.profiles_dir.resolve()
        profile_root = (profiles_root / profile_id).resolve()
        if not profile_root.is_relative_to(profiles_root):
            raise ValueError(f"Invalid profile root: {profile_id}")
        return profile_root

    def _resolve_key(self, *, profile_id: str, create_if_missing: bool) -> bytes | None:
        key_path = self.key_path(profile_id)
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

    @staticmethod
    def _normalize_secrets(raw: Mapping[str, object]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for key, value in raw.items():
            if key not in _PROFILE_RUNTIME_SECRET_FIELDS or not isinstance(value, str):
                continue
            stripped = value.strip()
            if not stripped:
                continue
            normalized[key] = stripped
        return normalized

    @staticmethod
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



def provider_secret_field(provider_id: str) -> str:
    """Map one provider id to its provider-specific API key field name."""

    normalized = provider_id.strip().lower()
    if normalized == "openrouter":
        return "openrouter_api_key"
    if normalized == "openai":
        return "openai_api_key"
    if normalized == "openai-codex":
        return "openai_codex_api_key"
    if normalized == "claude":
        return "claude_api_key"
    if normalized == "moonshot":
        return "moonshot_api_key"
    if normalized == "deepseek":
        return "deepseek_api_key"
    if normalized == "xai":
        return "xai_api_key"
    if normalized == "qwen":
        return "qwen_api_key"
    if normalized == "minimax-portal":
        return "minimax_portal_api_key"
    if normalized == "github-copilot":
        return "github_copilot_api_key"
    if normalized == "custom":
        return "custom_api_key"
    return "llm_api_key"


def provider_oauth_metadata_fields(provider_id: str) -> tuple[str, ...]:
    """Return provider-specific OAuth metadata secret fields."""

    normalized = provider_id.strip().lower()
    if normalized == "minimax-portal":
        return (
            "minimax_portal_refresh_token",
            "minimax_portal_token_expires_at",
            "minimax_portal_resource_url",
            "minimax_portal_region",
        )
    return ()


def get_profile_runtime_secrets_service(settings: Settings) -> ProfileRuntimeSecretsService:
    """Return cached profile runtime secrets service for one root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ProfileRuntimeSecretsService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_profile_runtime_secrets_services() -> None:
    """Reset cached profile runtime secrets services for tests."""

    _SERVICES_BY_ROOT.clear()
