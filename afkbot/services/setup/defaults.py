"""Runtime-store and env default resolution helpers for setup CLI."""

from __future__ import annotations

import os

from afkbot.services.setup.contracts import (
    PolicyFileAccessMode,
    PolicyNetworkMode,
    WILDCARD_NETWORK_HOST,
)
from afkbot.services.policy.presets_contracts import PolicyCapabilityId
from afkbot.services.setup.runtime_store import read_runtime_config, read_runtime_secrets
from afkbot.settings import Settings

_RECOMMENDED_POLICY_CAPABILITY_IDS: tuple[PolicyCapabilityId, ...] = (
    PolicyCapabilityId.MEMORY,
    PolicyCapabilityId.CREDENTIALS,
    PolicyCapabilityId.SUBAGENTS,
    PolicyCapabilityId.AUTOMATION,
    PolicyCapabilityId.TASKFLOW,
    PolicyCapabilityId.HTTP,
    PolicyCapabilityId.WEB,
    PolicyCapabilityId.BROWSER,
    PolicyCapabilityId.SKILLS,
    PolicyCapabilityId.APPS,
    PolicyCapabilityId.MCP,
)


def load_env_defaults(*, settings: Settings) -> dict[str, str]:
    """Merge persisted runtime store defaults with current environment overrides."""
    runtime_config = read_runtime_config(settings)
    runtime_secrets = read_runtime_secrets(settings)
    raw_policy_capabilities = runtime_config.get("policy_capabilities")
    policy_capabilities_default = normalize_runtime_string_seq(raw_policy_capabilities)
    if raw_policy_capabilities is None:
        policy_capabilities_default = recommended_policy_capabilities()
    raw_policy_network_allowlist = runtime_config.get("policy_network_allowlist")
    policy_network_allowlist_default = normalize_runtime_string_seq(raw_policy_network_allowlist)
    if raw_policy_network_allowlist is None:
        policy_network_allowlist_default = (WILDCARD_NETWORK_HOST,)
    merged = {
        "AFKBOT_PROMPT_LANGUAGE": str(runtime_config.get("prompt_language", "")),
        "AFKBOT_LLM_PROVIDER": str(runtime_config.get("llm_provider", settings.llm_provider)),
        "AFKBOT_LLM_MODEL": str(runtime_config.get("llm_model", settings.llm_model)),
        "AFKBOT_LLM_BASE_URL": str(runtime_config.get("llm_base_url", settings.llm_base_url or "")),
        "AFKBOT_LLM_PROXY_TYPE": str(runtime_config.get("llm_proxy_type", settings.llm_proxy_type)),
        "AFKBOT_LLM_PROXY_URL": str(runtime_config.get("llm_proxy_url", settings.llm_proxy_url or "")),
        "AFKBOT_CREDENTIALS_MASTER_KEYS": str(
            runtime_secrets.get("credentials_master_keys", settings.credentials_master_keys or "")
        ),
        "AFKBOT_DB_URL": str(runtime_config.get("db_url", settings.db_url)),
        "AFKBOT_RUNTIME_HOST": str(runtime_config.get("runtime_host", settings.runtime_host)),
        "AFKBOT_RUNTIME_PORT": str(runtime_config.get("runtime_port", settings.runtime_port)),
        "AFKBOT_NGINX_ENABLED": (
            "1"
            if coerce_bool(runtime_config.get("nginx_enabled"), default=settings.nginx_enabled)
            else "0"
        ),
        "AFKBOT_NGINX_PORT": str(runtime_config.get("nginx_port", settings.nginx_port)),
        "AFKBOT_NGINX_RUNTIME_HOST": str(runtime_config.get("nginx_runtime_host", "")),
        "AFKBOT_NGINX_RUNTIME_HTTPS": (
            "1" if coerce_bool(runtime_config.get("nginx_runtime_https"), default=False) else "0"
        ),
        "AFKBOT_NGINX_API_HOST": str(runtime_config.get("nginx_api_host", "")),
        "AFKBOT_NGINX_API_HTTPS": (
            "1" if coerce_bool(runtime_config.get("nginx_api_https"), default=False) else "0"
        ),
        "AFKBOT_CERTBOT_EMAIL": str(runtime_config.get("certbot_email", "")),
        "AFKBOT_PUBLIC_RUNTIME_URL": str(runtime_config.get("public_runtime_url", "")),
        "AFKBOT_PUBLIC_CHAT_API_URL": str(runtime_config.get("public_chat_api_url", "")),
        "AFKBOT_NGINX_CONFIG_PATH": str(runtime_config.get("nginx_config_path", "")),
        "AFKBOT_POLICY_ENABLED": (
            "1" if coerce_bool(runtime_config.get("policy_enabled"), default=True) else "0"
        ),
        "AFKBOT_POLICY_PRESET": str(runtime_config.get("policy_preset", "medium") or "medium"),
        "AFKBOT_POLICY_SETUP_MODE": str(
            runtime_config.get("policy_setup_mode", "recommended") or "recommended"
        ),
        "AFKBOT_POLICY_CONFIRMATION_MODE": str(
            runtime_config.get("policy_confirmation_mode", "destructive_files")
            or "destructive_files"
        ),
        "AFKBOT_POLICY_CAPABILITIES": ",".join(policy_capabilities_default),
        "AFKBOT_POLICY_FILE_ACCESS_MODE": str(
            runtime_config.get("policy_file_access_mode", PolicyFileAccessMode.READ_WRITE.value)
        ),
        "AFKBOT_POLICY_WORKSPACE_SCOPE": str(
            runtime_config.get("policy_workspace_scope", "profile_only")
        ),
        "AFKBOT_POLICY_NETWORK_MODE": str(
            runtime_config.get("policy_network_mode", PolicyNetworkMode.UNRESTRICTED.value)
        ),
        "AFKBOT_POLICY_NETWORK_ALLOWLIST": ",".join(policy_network_allowlist_default),
        "AFKBOT_AUTO_INSTALL_DEPS": (
            "1" if coerce_bool(runtime_config.get("auto_install_deps"), default=True) else "0"
        ),
        "AFKBOT_OPENROUTER_BASE_URL": str(
            runtime_config.get("openrouter_base_url", settings.openrouter_base_url)
        ),
        "AFKBOT_OPENAI_BASE_URL": str(runtime_config.get("openai_base_url", settings.openai_base_url)),
        "AFKBOT_OPENAI_CODEX_BASE_URL": str(
            runtime_config.get("openai_codex_base_url", settings.openai_codex_base_url)
        ),
        "AFKBOT_CLAUDE_BASE_URL": str(runtime_config.get("claude_base_url", settings.claude_base_url)),
        "AFKBOT_MOONSHOT_BASE_URL": str(runtime_config.get("moonshot_base_url", settings.moonshot_base_url)),
        "AFKBOT_DEEPSEEK_BASE_URL": str(
            runtime_config.get("deepseek_base_url", settings.deepseek_base_url)
        ),
        "AFKBOT_XAI_BASE_URL": str(runtime_config.get("xai_base_url", settings.xai_base_url)),
        "AFKBOT_QWEN_BASE_URL": str(runtime_config.get("qwen_base_url", settings.qwen_base_url)),
        "AFKBOT_MINIMAX_PORTAL_BASE_URL": str(
            runtime_config.get("minimax_portal_base_url", settings.minimax_portal_base_url)
        ),
        "AFKBOT_GITHUB_COPILOT_BASE_URL": str(
            runtime_config.get("github_copilot_base_url", settings.github_copilot_base_url)
        ),
        "AFKBOT_CUSTOM_BASE_URL": str(runtime_config.get("custom_base_url", settings.custom_base_url)),
        "AFKBOT_CUSTOM_INTERFACE": str(runtime_config.get("custom_interface", settings.custom_interface)),
        "AFKBOT_LLM_API_KEY": str(runtime_secrets.get("llm_api_key", settings.llm_api_key or "")),
        "AFKBOT_OPENROUTER_API_KEY": str(
            runtime_secrets.get("openrouter_api_key", settings.openrouter_api_key or "")
        ),
        "AFKBOT_OPENAI_API_KEY": str(runtime_secrets.get("openai_api_key", settings.openai_api_key or "")),
        "AFKBOT_OPENAI_CODEX_API_KEY": str(
            runtime_secrets.get("openai_codex_api_key", settings.openai_codex_api_key or "")
        ),
        "AFKBOT_CLAUDE_API_KEY": str(runtime_secrets.get("claude_api_key", settings.claude_api_key or "")),
        "AFKBOT_MOONSHOT_API_KEY": str(
            runtime_secrets.get("moonshot_api_key", settings.moonshot_api_key or "")
        ),
        "AFKBOT_DEEPSEEK_API_KEY": str(
            runtime_secrets.get("deepseek_api_key", settings.deepseek_api_key or "")
        ),
        "AFKBOT_XAI_API_KEY": str(runtime_secrets.get("xai_api_key", settings.xai_api_key or "")),
        "AFKBOT_QWEN_API_KEY": str(runtime_secrets.get("qwen_api_key", settings.qwen_api_key or "")),
        "AFKBOT_MINIMAX_PORTAL_API_KEY": str(
            runtime_secrets.get("minimax_portal_api_key", settings.minimax_portal_api_key or "")
        ),
        "AFKBOT_MINIMAX_PORTAL_REFRESH_TOKEN": str(
            runtime_secrets.get(
                "minimax_portal_refresh_token",
                settings.minimax_portal_refresh_token or "",
            )
        ),
        "AFKBOT_MINIMAX_PORTAL_TOKEN_EXPIRES_AT": str(
            runtime_secrets.get(
                "minimax_portal_token_expires_at",
                settings.minimax_portal_token_expires_at or "",
            )
        ),
        "AFKBOT_MINIMAX_PORTAL_RESOURCE_URL": str(
            runtime_secrets.get(
                "minimax_portal_resource_url",
                settings.minimax_portal_resource_url or "",
            )
        ),
        "AFKBOT_MINIMAX_PORTAL_REGION": str(
            runtime_secrets.get("minimax_portal_region", settings.minimax_portal_region or "")
        ),
        "AFKBOT_GITHUB_COPILOT_API_KEY": str(
            runtime_secrets.get("github_copilot_api_key", settings.github_copilot_api_key or "")
        ),
        "AFKBOT_CUSTOM_API_KEY": str(runtime_secrets.get("custom_api_key", settings.custom_api_key or "")),
    }
    for key in tuple(merged.keys()):
        env_value = os.getenv(key)
        if env_value is not None:
            merged[key] = env_value
    return merged


def coerce_bool(value: object, *, default: bool) -> bool:
    """Coerce runtime-store values into bool with fallback."""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return read_bool_default(value, default)
    return default


def normalize_runtime_string_seq(raw: object) -> tuple[str, ...]:
    """Normalize comma-separated or list-like runtime-store string sequences."""

    if isinstance(raw, str):
        candidates = raw.split(",")
    elif isinstance(raw, (list, tuple, set)):
        candidates = [str(item) for item in raw]
    else:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = item.strip().lower()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def recommended_policy_capabilities() -> tuple[str, ...]:
    """Return recommended capability ids used by setup defaults and prompts.

    Recommended setup keeps medium preset guardrails while excluding the
    highest-risk capability groups from the default capability set.
    """

    return tuple(capability.value for capability in _RECOMMENDED_POLICY_CAPABILITY_IDS)


def read_int_default(raw: str | None, fallback: int) -> int:
    """Parse integer default from persisted string, falling back safely."""

    if raw is None:
        return fallback
    try:
        value = int(raw.strip())
    except ValueError:
        return fallback
    if not (1 <= value <= 65535):
        return fallback
    return value


def read_bool_default(raw: str | None, fallback: bool) -> bool:
    """Parse boolean default from persisted string, falling back safely."""

    if raw is None:
        return fallback
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return fallback
