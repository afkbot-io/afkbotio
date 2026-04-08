"""Contracts and enums shared by setup CLI helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from afkbot.services.profile_runtime import ProfileRuntimeConfig


WILDCARD_NETWORK_HOST = "*"
NETWORK_POLICY_RECOMMENDED_HOSTS: dict[str, tuple[str, ...]] = {
    "web": ("api.search.brave.com",),
    "apps": ("api.telegram.org",),
}


class PolicySetupMode(StrEnum):
    """Interactive setup security mode."""

    RECOMMENDED = "recommended"
    CUSTOM = "custom"


class PolicyNetworkMode(StrEnum):
    """High-level outbound network access mode."""

    UNRESTRICTED = "unrestricted"
    RECOMMENDED = "recommended"
    CUSTOM = "custom"
    DENY_ALL = "deny_all"


class PolicyFileAccessMode(StrEnum):
    """High-level direct file-tool access mode."""

    NONE = "none"
    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


@dataclass(slots=True, frozen=True)
class SetupConfig:
    """Normalized setup answers and flags passed through setup runtime services."""

    env_file: Path
    db_url: str
    prompt_language: str
    llm_provider: str
    chat_model: str
    llm_thinking_level: str
    llm_api_key: str
    llm_base_url: str
    custom_interface: str
    llm_proxy_type: str
    llm_proxy_url: str
    credentials_master_keys: str
    runtime_host: str
    runtime_port: int
    nginx_enabled: bool
    nginx_port: int
    nginx_runtime_host: str
    nginx_runtime_public_port: int | None
    nginx_runtime_https: bool
    nginx_api_host: str
    nginx_api_public_port: int | None
    nginx_api_https: bool
    certbot_email: str
    public_runtime_url: str
    public_chat_api_url: str
    policy_setup_mode: str
    policy_enabled: bool
    policy_preset: str
    policy_confirmation_mode: str
    policy_capabilities: tuple[str, ...]
    policy_file_access_mode: str
    policy_workspace_scope_mode: str
    policy_allowed_directories: tuple[str, ...]
    policy_network_mode: str
    policy_network_allowlist: tuple[str, ...]
    default_profile_runtime_config: ProfileRuntimeConfig
    auto_install_deps: bool
    update_notices_enabled: bool
    runtime_secrets_update: dict[str, str] = field(default_factory=dict)
