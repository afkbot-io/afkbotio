"""Default-building helpers for profile mutation flows."""

from __future__ import annotations

import os
from pathlib import Path

from afkbot.services.policy import PolicyPresetLevel, infer_workspace_scope_mode
from afkbot.services.profile_runtime import ProfileDetails, ProfileRuntimeConfig
from afkbot.services.setup.contracts import (
    PolicyFileAccessMode,
    PolicySetupMode,
    WILDCARD_NETWORK_HOST,
)
from afkbot.services.setup.defaults import recommended_policy_capabilities
from afkbot.services.setup.policy_inputs import default_policy_network_mode


def build_profile_defaults(defaults: dict[str, str]) -> dict[str, str]:
    """Return profile defaults without inheriting permissive setup-only policy."""

    resolved = dict(defaults)
    for key in (
        "AFKBOT_POLICY_ENABLED",
        "AFKBOT_POLICY_CAPABILITIES",
        "AFKBOT_POLICY_SETUP_MODE",
        "AFKBOT_POLICY_CONFIRMATION_MODE",
        "AFKBOT_POLICY_FILE_ACCESS_MODE",
    ):
        if os.getenv(key) is None:
            resolved.pop(key, None)
    if os.getenv("AFKBOT_POLICY_NETWORK_MODE") is None:
        resolved["AFKBOT_POLICY_NETWORK_MODE"] = "recommended"
    if os.getenv("AFKBOT_POLICY_NETWORK_ALLOWLIST") is None:
        resolved["AFKBOT_POLICY_NETWORK_ALLOWLIST"] = ""
    return resolved


def build_policy_defaults_from_details(*, root_dir: Path, details: ProfileDetails) -> dict[str, str]:
    """Build policy-default map from one persisted profile for interactive update."""

    policy_setup_mode = (
        PolicySetupMode.RECOMMENDED.value
        if (
            details.policy.enabled is True
            and details.policy.preset == PolicyPresetLevel.MEDIUM.value
            and details.policy.capabilities == recommended_policy_capabilities()
            and details.policy.network_allowlist == (WILDCARD_NETWORK_HOST,)
            and details.policy.file_access_mode == PolicyFileAccessMode.READ_WRITE.value
            and infer_workspace_scope_mode(
                root_dir=root_dir,
                profile_root=Path(details.profile_root),
                allowed_directories=details.policy.allowed_directories,
            )
            == "profile_only"
        )
        else PolicySetupMode.CUSTOM.value
    )
    defaults = {
        "AFKBOT_POLICY_ENABLED": "1" if details.policy.enabled else "0",
        "AFKBOT_POLICY_PRESET": details.policy.preset,
        "AFKBOT_POLICY_SETUP_MODE": policy_setup_mode,
        "AFKBOT_POLICY_CAPABILITIES": ",".join(details.policy.capabilities),
        "AFKBOT_POLICY_FILE_ACCESS_MODE": details.policy.file_access_mode,
        "AFKBOT_POLICY_WORKSPACE_SCOPE": infer_workspace_scope_mode(
            root_dir=root_dir,
            profile_root=Path(details.profile_root),
            allowed_directories=details.policy.allowed_directories,
        ),
        "AFKBOT_POLICY_NETWORK_ALLOWLIST": ",".join(details.policy.network_allowlist),
    }
    defaults["AFKBOT_POLICY_NETWORK_MODE"] = default_policy_network_mode(
        defaults=defaults,
        capabilities=details.policy.capabilities,
    )
    return defaults


def build_runtime_defaults_from_details(details: ProfileDetails) -> dict[str, str]:
    """Build runtime-default map from one persisted profile for setup/update flows."""

    runtime = resolve_current_runtime_config(details)
    return {
        "AFKBOT_LLM_PROVIDER": runtime.llm_provider,
        "AFKBOT_LLM_MODEL": runtime.llm_model,
        "AFKBOT_LLM_BASE_URL": runtime.llm_base_url or "",
        "AFKBOT_CUSTOM_INTERFACE": runtime.custom_interface,
        "AFKBOT_LLM_PROXY_TYPE": runtime.llm_proxy_type,
        "AFKBOT_LLM_PROXY_URL": runtime.llm_proxy_url or "",
        "AFKBOT_LLM_THINKING_LEVEL": runtime.llm_thinking_level or "medium",
    }


def resolve_current_runtime_config(details: ProfileDetails) -> ProfileRuntimeConfig:
    """Return persisted runtime config or reconstruct one from effective runtime fields."""

    if details.runtime_config is not None:
        return details.runtime_config
    runtime = details.effective_runtime
    return ProfileRuntimeConfig(
        llm_provider=runtime.llm_provider,
        llm_model=runtime.llm_model,
        llm_base_url=runtime.llm_base_url,
        custom_interface=runtime.custom_interface,
        llm_proxy_type=runtime.llm_proxy_type,
        llm_proxy_url=runtime.llm_proxy_url,
        llm_thinking_level=runtime.llm_thinking_level,
        llm_history_turns=runtime.llm_history_turns,
        chat_planning_mode=runtime.chat_planning_mode,
        enabled_tool_plugins=runtime.enabled_tool_plugins,
        memory_auto_search_enabled=runtime.memory_auto_search_enabled,
        memory_auto_search_scope_mode=runtime.memory_auto_search_scope_mode,
        memory_auto_search_limit=runtime.memory_auto_search_limit,
        memory_auto_search_include_global=runtime.memory_auto_search_include_global,
        memory_auto_search_chat_limit=runtime.memory_auto_search_chat_limit,
        memory_auto_search_global_limit=runtime.memory_auto_search_global_limit,
        memory_global_fallback_enabled=runtime.memory_global_fallback_enabled,
        memory_auto_context_item_chars=runtime.memory_auto_context_item_chars,
        memory_auto_save_enabled=runtime.memory_auto_save_enabled,
        memory_auto_save_scope_mode=runtime.memory_auto_save_scope_mode,
        memory_auto_promote_enabled=runtime.memory_auto_promote_enabled,
        memory_auto_save_kinds=runtime.memory_auto_save_kinds,
        memory_auto_save_max_chars=runtime.memory_auto_save_max_chars,
        session_compaction_enabled=runtime.session_compaction_enabled,
        session_compaction_trigger_turns=runtime.session_compaction_trigger_turns,
        session_compaction_keep_recent_turns=runtime.session_compaction_keep_recent_turns,
        session_compaction_max_chars=runtime.session_compaction_max_chars,
        session_compaction_prune_raw_turns=runtime.session_compaction_prune_raw_turns,
    )


__all__ = [
    "build_policy_defaults_from_details",
    "build_profile_defaults",
    "build_runtime_defaults_from_details",
    "resolve_current_runtime_config",
]
