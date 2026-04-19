"""Contracts for profile-scoped runtime configuration and views."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileRuntimeConfig(BaseModel):
    """Persisted profile-local runtime overrides applied on top of global settings."""

    model_config = ConfigDict(extra="forbid")

    llm_provider: Literal[
        "openrouter",
        "openai",
        "openai-codex",
        "claude",
        "moonshot",
        "deepseek",
        "xai",
        "qwen",
        "minimax-portal",
        "github-copilot",
        "custom",
    ]
    llm_model: str = Field(min_length=1)
    llm_base_url: str | None = None
    custom_interface: Literal["openai"] = "openai"
    llm_proxy_type: Literal["none", "http", "socks5", "socks5h"] = "none"
    llm_proxy_url: str | None = None
    llm_thinking_level: Literal["low", "medium", "high", "very_high"] | None = None
    llm_history_turns: int | None = None
    chat_planning_mode: Literal["off", "auto", "on"] | None = None
    chat_secret_guard_enabled: bool | None = None
    enabled_tool_plugins: tuple[str, ...] | None = None
    taskflow_team_profile_ids: tuple[str, ...] | None = None
    memory_core_enabled: bool | None = None
    memory_core_max_items: int | None = None
    memory_core_max_chars: int | None = None
    memory_auto_search_enabled: bool | None = None
    memory_auto_search_scope_mode: Literal["auto", "profile", "chat", "thread", "user_in_chat"] | None = None
    memory_auto_search_limit: int | None = None
    memory_auto_search_include_global: bool | None = None
    memory_auto_search_chat_limit: int | None = None
    memory_auto_search_global_limit: int | None = None
    memory_global_fallback_enabled: bool | None = None
    memory_auto_context_item_chars: int | None = None
    memory_auto_save_enabled: bool | None = None
    memory_auto_save_scope_mode: Literal["auto", "profile", "chat", "thread", "user_in_chat"] | None = None
    memory_auto_promote_enabled: bool | None = None
    memory_auto_save_kinds: tuple[
        Literal["fact", "preference", "decision", "task", "risk", "note"], ...
    ] | None = None
    memory_auto_save_max_chars: int | None = None
    memory_recall_enabled: bool | None = None
    session_compaction_enabled: bool | None = None
    session_compaction_trigger_turns: int | None = None
    session_compaction_keep_recent_turns: int | None = None
    session_compaction_max_chars: int | None = None
    session_compaction_prune_raw_turns: bool | None = None

    @field_validator("llm_model", "llm_base_url", "llm_proxy_url", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("enabled_tool_plugins", "taskflow_team_profile_ids", mode="before")
    @classmethod
    def _normalize_enabled_tool_plugins(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            name = str(item).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            normalized.append(name)
        return tuple(normalized) or None

    @field_validator("memory_auto_save_kinds", mode="before")
    @classmethod
    def _normalize_memory_auto_save_kinds(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...] | None:
        if value is None:
            return None
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            name = str(item).strip().lower()
            if not name or name in seen:
                continue
            seen.add(name)
            normalized.append(name)
        return tuple(normalized) or None

    @field_validator(
        "memory_auto_search_limit",
        "memory_auto_search_chat_limit",
        "memory_auto_search_global_limit",
        "memory_auto_context_item_chars",
        "memory_auto_save_max_chars",
        "memory_core_max_items",
        "memory_core_max_chars",
        "session_compaction_trigger_turns",
        "session_compaction_keep_recent_turns",
        "session_compaction_max_chars",
    )
    @classmethod
    def _validate_optional_positive_int(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 1:
            raise ValueError("runtime override limits must be >= 1")
        return value

    @field_validator("llm_history_turns")
    @classmethod
    def _validate_optional_non_negative_history_turns(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("llm_history_turns override must be >= 0")
        return value


class ProfileRuntimeResolved(BaseModel):
    """Effective runtime configuration used by one profile at execution time."""

    llm_provider: str
    llm_model: str
    llm_base_url: str | None = None
    custom_interface: str = "openai"
    llm_proxy_type: str = "none"
    llm_proxy_url: str | None = None
    llm_thinking_level: Literal["low", "medium", "high", "very_high"] = "medium"
    llm_history_turns: int = 8
    chat_planning_mode: Literal["off", "auto", "on"] = "auto"
    chat_secret_guard_enabled: bool = False
    enabled_tool_plugins: tuple[str, ...]
    memory_core_enabled: bool = False
    memory_core_max_items: int = 8
    memory_core_max_chars: int = 600
    memory_auto_search_enabled: bool = False
    memory_auto_search_scope_mode: Literal["auto", "profile", "chat", "thread", "user_in_chat"] = "auto"
    memory_auto_search_limit: int = 3
    memory_auto_search_include_global: bool = True
    memory_auto_search_chat_limit: int = 3
    memory_auto_search_global_limit: int = 2
    memory_global_fallback_enabled: bool = True
    memory_auto_context_item_chars: int = 240
    memory_auto_save_enabled: bool = False
    memory_auto_save_scope_mode: Literal["auto", "profile", "chat", "thread", "user_in_chat"] = "auto"
    memory_auto_promote_enabled: bool = False
    memory_auto_save_kinds: tuple[
        Literal["fact", "preference", "decision", "task", "risk", "note"], ...
    ] = ("fact", "preference", "decision", "task", "risk", "note")
    memory_auto_save_max_chars: int = 1000
    memory_recall_enabled: bool = False
    session_compaction_enabled: bool = False
    session_compaction_trigger_turns: int = 12
    session_compaction_keep_recent_turns: int = 6
    session_compaction_max_chars: int = 4000
    session_compaction_prune_raw_turns: bool = False
    provider_api_key_configured: bool = False
    brave_api_key_configured: bool = False


class ProfileRuntimeSecretsView(BaseModel):
    """Serializable status for profile-local runtime secrets."""

    configured_fields: tuple[str, ...] = ()
    has_profile_secrets: bool = False


class ProfilePolicyView(BaseModel):
    """Serializable profile policy summary for CLI/API inspection."""

    enabled: bool = True
    preset: str = "medium"
    capabilities: tuple[str, ...] = ()
    file_access_mode: str = "read_write"
    allowed_directories: tuple[str, ...] = ()
    network_allowlist: tuple[str, ...] = ()


class ProfileSummary(BaseModel):
    """Lightweight summary of a runtime profile."""

    id: str
    name: str
    is_default: bool = False
    status: str = "active"
    has_runtime_config: bool = False
    effective_runtime: ProfileRuntimeResolved


class ProfileDetails(ProfileSummary):
    """Detailed runtime profile view including stored config and paths."""

    profile_root: str
    system_dir: str
    runtime_config: ProfileRuntimeConfig | None = None
    runtime_config_path: str
    runtime_secrets: ProfileRuntimeSecretsView
    runtime_secrets_path: str
    bootstrap_dir: str
    skills_dir: str
    subagents_dir: str
    policy: ProfilePolicyView


class ProfileBootstrapFileView(BaseModel):
    """Summary for one profile bootstrap override file."""

    file_name: str
    path: str
    exists: bool = False


class ProfileBootstrapRecord(ProfileBootstrapFileView):
    """Detailed bootstrap file view including content."""

    content: str | None = None
