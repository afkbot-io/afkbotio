"""Default-building helpers for profile mutation flows."""

from __future__ import annotations

import os
from pathlib import Path

from afkbot.services.policy import infer_workspace_scope_mode
from afkbot.services.profile_runtime import ProfileDetails, ProfileRuntimeConfig
from afkbot.services.setup.contracts import (
    PolicyNetworkMode,
    PolicySetupMode,
)
from afkbot.services.setup.policy_inputs import (
    default_policy_network_mode,
    recommended_policy_network_hosts,
)
from afkbot.services.wizard.profile_intent_mapper import (
    map_profile_intent_to_policy,
    quick_safe_profile_intent_selection,
)


def build_profile_defaults(defaults: dict[str, str]) -> dict[str, str]:
    """Return profile defaults without inheriting permissive setup-only policy."""

    resolved = dict(defaults)
    for key in (
        "AFKBOT_POLICY_ENABLED",
        "AFKBOT_POLICY_CAPABILITIES",
        "AFKBOT_POLICY_SETUP_MODE",
        "AFKBOT_POLICY_CONFIRMATION_MODE",
        "AFKBOT_POLICY_FILE_ACCESS_MODE",
        "AFKBOT_WIZARD_SETUP_DEPTH",
        "AFKBOT_WIZARD_WORK_CONTEXTS",
        "AFKBOT_WIZARD_ACTIONS",
        "AFKBOT_WIZARD_ISOLATION",
        "AFKBOT_WIZARD_CONFIRMATION",
        "AFKBOT_WIZARD_NETWORK",
        "AFKBOT_WIZARD_NETWORK_ALLOWLIST",
    ):
        if os.getenv(key) is None:
            resolved.pop(key, None)
    if os.getenv("AFKBOT_POLICY_NETWORK_MODE") is None:
        resolved["AFKBOT_POLICY_NETWORK_MODE"] = "recommended"
    if os.getenv("AFKBOT_POLICY_NETWORK_ALLOWLIST") is None:
        resolved["AFKBOT_POLICY_NETWORK_ALLOWLIST"] = ""
    return resolved


def build_policy_defaults_from_details(
    *, root_dir: Path, details: ProfileDetails
) -> dict[str, str]:
    """Build policy-default map from one persisted profile for interactive update."""

    quick_selection = quick_safe_profile_intent_selection()
    quick_policy = map_profile_intent_to_policy(quick_selection)
    quick_network_allowlist = (
        ("*",)
        if quick_policy.network_mode == PolicyNetworkMode.UNRESTRICTED.value
        else recommended_policy_network_hosts(capabilities=quick_policy.capabilities)
    )
    workspace_scope_mode = infer_workspace_scope_mode(
        root_dir=root_dir,
        profile_root=Path(details.profile_root),
        allowed_directories=details.policy.allowed_directories,
    )
    policy_setup_mode = (
        PolicySetupMode.RECOMMENDED.value
        if (
            details.policy.enabled is True
            and details.policy.preset == quick_policy.preset
            and details.policy.capabilities == quick_policy.capabilities
            and details.policy.network_allowlist == quick_network_allowlist
            and details.policy.file_access_mode == quick_policy.file_access_mode
            and workspace_scope_mode == quick_policy.workspace_scope_mode
            and details.policy.shell_sandbox_mode == quick_policy.shell_sandbox_mode
            and details.policy.shell_allowed_commands == quick_policy.shell_allowed_commands
        )
        else PolicySetupMode.CUSTOM.value
    )
    defaults = {
        "AFKBOT_POLICY_ENABLED": "1" if details.policy.enabled else "0",
        "AFKBOT_POLICY_PRESET": details.policy.preset,
        "AFKBOT_POLICY_SETUP_MODE": policy_setup_mode,
        "AFKBOT_POLICY_CAPABILITIES": ",".join(details.policy.capabilities),
        "AFKBOT_POLICY_FILE_ACCESS_MODE": details.policy.file_access_mode,
        "AFKBOT_POLICY_WORKSPACE_SCOPE": workspace_scope_mode,
        "AFKBOT_POLICY_SHELL_SANDBOX_MODE": details.policy.shell_sandbox_mode,
        "AFKBOT_POLICY_SHELL_ALLOWED_COMMANDS": ",".join(details.policy.shell_allowed_commands),
        "AFKBOT_POLICY_NETWORK_ALLOWLIST": ",".join(details.policy.network_allowlist),
    }
    defaults["AFKBOT_POLICY_NETWORK_MODE"] = default_policy_network_mode(
        defaults=defaults,
        capabilities=details.policy.capabilities,
    )
    runtime = resolve_current_runtime_config(details)
    defaults.update(
        {
            "AFKBOT_WIZARD_SETUP_DEPTH": runtime.wizard_setup_depth or "legacy",
            "AFKBOT_WIZARD_WORK_CONTEXTS": ",".join(runtime.wizard_work_contexts or ()),
            "AFKBOT_WIZARD_ACTIONS": ",".join(runtime.wizard_actions or ()),
            "AFKBOT_WIZARD_ISOLATION": runtime.wizard_isolation or "",
            "AFKBOT_WIZARD_CONFIRMATION": runtime.wizard_confirmation or "",
            "AFKBOT_WIZARD_NETWORK": runtime.wizard_network or "",
            "AFKBOT_WIZARD_NETWORK_ALLOWLIST": ",".join(runtime.wizard_network_allowlist or ()),
        }
    )
    if policy_setup_mode == PolicySetupMode.RECOMMENDED.value and defaults[
        "AFKBOT_WIZARD_SETUP_DEPTH"
    ] in {"", "legacy"}:
        defaults["AFKBOT_WIZARD_SETUP_DEPTH"] = quick_selection.depth
        defaults["AFKBOT_WIZARD_WORK_CONTEXTS"] = ",".join(quick_selection.work_contexts)
        defaults["AFKBOT_WIZARD_ACTIONS"] = ",".join(quick_selection.actions)
        defaults["AFKBOT_WIZARD_ISOLATION"] = quick_selection.isolation
        defaults["AFKBOT_WIZARD_CONFIRMATION"] = quick_selection.confirmation
        defaults["AFKBOT_WIZARD_NETWORK"] = quick_selection.network
        defaults["AFKBOT_WIZARD_NETWORK_ALLOWLIST"] = ",".join(
            quick_selection.network_allowlist
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
        "AFKBOT_CHAT_SECRET_GUARD_ENABLED": "1" if runtime.chat_secret_guard_enabled else "0",
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
        chat_secret_guard_enabled=runtime.chat_secret_guard_enabled,
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
