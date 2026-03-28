"""Filesystem-backed profile runtime configuration store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from afkbot.services.atomic_writes import atomic_json_write
from afkbot.services.llm.provider_catalog import parse_provider
from afkbot.services.memory.contracts import MemoryKind
from afkbot.services.llm.provider_settings import resolve_api_key, resolve_base_url
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.profile_runtime.contracts import (
    ProfileRuntimeConfig,
    ProfileRuntimeResolved,
)
from afkbot.services.profile_runtime.runtime_secrets import get_profile_runtime_secrets_service
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "ProfileRuntimeConfigService"] = {}
PROFILE_RUNTIME_CONFIG_VERSION = 1
_PROFILE_RUNTIME_CONFIG_FILENAME = "agent_config.json"


class ProfileRuntimeConfigService:
    """Read, write, and resolve profile-local runtime configuration."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runtime_secrets = get_profile_runtime_secrets_service(settings)

    def profile_root(self, profile_id: str) -> Path:
        """Return safe absolute path to one profile root directory."""

        return self._safe_profile_root(profile_id)

    def system_dir(self, profile_id: str) -> Path:
        """Return safe absolute path to one profile-local system directory."""

        return self.profile_root(profile_id) / ".system"

    def config_path(self, profile_id: str) -> Path:
        """Return safe absolute path to one profile runtime config file."""

        return self.system_dir(profile_id) / _PROFILE_RUNTIME_CONFIG_FILENAME

    def bootstrap_dir(self, profile_id: str) -> Path:
        """Return safe absolute path to one profile bootstrap directory."""

        return self.profile_root(profile_id) / "bootstrap"

    def skills_dir(self, profile_id: str) -> Path:
        """Return safe absolute path to one profile skills directory."""

        return self.profile_root(profile_id) / "skills"

    def subagents_dir(self, profile_id: str) -> Path:
        """Return safe absolute path to one profile subagents directory."""

        return self.profile_root(profile_id) / "subagents"

    def ensure_layout(self, profile_id: str) -> None:
        """Ensure canonical profile-agent directory layout exists on disk."""

        for path in (
            self.profile_root(profile_id),
            self.system_dir(profile_id),
            self.bootstrap_dir(profile_id),
            self.skills_dir(profile_id),
            self.subagents_dir(profile_id),
        ):
            path.mkdir(parents=True, exist_ok=True)

    def load(self, profile_id: str) -> ProfileRuntimeConfig | None:
        """Load stored runtime config for one profile, if present."""

        path = self.config_path(profile_id)
        if not path.exists():
            return None
        payload = self._read_json_object(path)
        version = payload.get("version")
        if version != PROFILE_RUNTIME_CONFIG_VERSION:
            raise ValueError(f"Unsupported profile runtime config version in {path}")
        raw_config = payload.get("config")
        if not isinstance(raw_config, dict):
            raise ValueError(f"Invalid profile runtime config payload in {path}")
        return ProfileRuntimeConfig.model_validate(raw_config)

    def write(self, profile_id: str, config: ProfileRuntimeConfig) -> Path:
        """Persist one profile runtime config atomically."""

        self.ensure_layout(profile_id)
        path = self.config_path(profile_id)
        payload = {
            "version": PROFILE_RUNTIME_CONFIG_VERSION,
            "updated_at": datetime.now(tz=UTC).isoformat(),
            "config": config.model_dump(mode="json", exclude_none=True),
        }
        atomic_json_write(path, payload, mode=0o600)
        return path

    def remove(self, profile_id: str) -> None:
        """Delete profile runtime config file when it exists."""

        path = self.config_path(profile_id)
        if path.exists():
            path.unlink()

    def build_effective_settings(
        self,
        *,
        profile_id: str,
        base_settings: Settings | None = None,
        ensure_layout: bool = False,
    ) -> Settings:
        """Apply stored profile overrides on top of base runtime settings."""

        if ensure_layout:
            self.ensure_layout(profile_id)
        settings = base_settings or self._settings
        config = self.load(profile_id)
        effective_settings = settings if config is None else self.apply_to_settings(settings=settings, config=config)
        secrets = self._runtime_secrets.load(profile_id)
        if not secrets:
            return effective_settings
        return self._runtime_secrets.apply_to_settings(settings=effective_settings, secrets=secrets)

    @staticmethod
    def apply_to_settings(*, settings: Settings, config: ProfileRuntimeConfig) -> Settings:
        """Build a validated settings object with profile overrides applied."""

        merged = settings.model_dump()
        merged.update(config.model_dump(exclude_none=True))
        if config.llm_base_url:
            provider_field_by_id = {
                "openrouter": "openrouter_base_url",
                "openai": "openai_base_url",
                "deepseek": "deepseek_base_url",
                "xai": "xai_base_url",
                "qwen": "qwen_base_url",
                "custom": "custom_base_url",
            }
            provider_field = provider_field_by_id.get(config.llm_provider)
            if provider_field is not None:
                merged[provider_field] = config.llm_base_url
        return Settings(**merged)

    @staticmethod
    def resolved_runtime(settings: Settings) -> ProfileRuntimeResolved:
        """Project settings object into a serializable runtime summary."""

        provider_id = parse_provider(settings.llm_provider)
        return ProfileRuntimeResolved(
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            llm_base_url=resolve_base_url(settings=settings, provider_id=provider_id),
            custom_interface=settings.custom_interface,
            llm_proxy_type=settings.llm_proxy_type,
            llm_proxy_url=settings.llm_proxy_url,
            llm_thinking_level=settings.llm_thinking_level,
            llm_history_turns=settings.llm_history_turns,
            chat_planning_mode=settings.chat_planning_mode,
            enabled_tool_plugins=tuple(settings.enabled_tool_plugins),
            memory_auto_search_enabled=settings.memory_auto_search_enabled,
            memory_auto_search_scope_mode=settings.memory_auto_search_scope_mode,
            memory_auto_search_limit=settings.memory_auto_search_limit,
            memory_auto_search_include_global=settings.memory_auto_search_include_global,
            memory_auto_search_chat_limit=settings.memory_auto_search_chat_limit,
            memory_auto_search_global_limit=settings.memory_auto_search_global_limit,
            memory_global_fallback_enabled=settings.memory_global_fallback_enabled,
            memory_auto_context_item_chars=settings.memory_auto_context_item_chars,
            memory_auto_save_enabled=settings.memory_auto_save_enabled,
            memory_auto_save_scope_mode=settings.memory_auto_save_scope_mode,
            memory_auto_promote_enabled=settings.memory_auto_promote_enabled,
            memory_auto_save_kinds=cast(tuple[MemoryKind, ...], settings.memory_auto_save_kinds),
            memory_auto_save_max_chars=settings.memory_auto_save_max_chars,
            session_compaction_enabled=settings.session_compaction_enabled,
            session_compaction_trigger_turns=settings.session_compaction_trigger_turns,
            session_compaction_keep_recent_turns=settings.session_compaction_keep_recent_turns,
            session_compaction_max_chars=settings.session_compaction_max_chars,
            session_compaction_prune_raw_turns=settings.session_compaction_prune_raw_turns,
            provider_api_key_configured=bool(resolve_api_key(settings=settings, provider_id=provider_id)),
            brave_api_key_configured=bool((settings.brave_api_key or "").strip()),
        )

    def _safe_profile_root(self, profile_id: str) -> Path:
        validate_profile_id(profile_id)
        profiles_root = self._settings.profiles_dir.resolve()
        profile_root = (profiles_root / profile_id).resolve()
        if not profile_root.is_relative_to(profiles_root):
            raise ValueError(f"Invalid profile root: {profile_id}")
        return profile_root

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid JSON object in {path}")
        return payload



def get_profile_runtime_config_service(settings: Settings) -> ProfileRuntimeConfigService:
    """Return cached profile runtime config service for one root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ProfileRuntimeConfigService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_profile_runtime_config_services() -> None:
    """Reset cached profile runtime config services for tests."""

    _SERVICES_BY_ROOT.clear()
