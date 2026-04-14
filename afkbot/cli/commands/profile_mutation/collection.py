"""Collection helpers shared by profile add, update, and setup command flows."""

from __future__ import annotations

from pathlib import Path

from afkbot.cli.commands.profile_common import resolve_profile_name
from afkbot.cli.commands.profile_mutation.contracts import CollectedProfileMutationInputs
from afkbot.cli.commands.profile_mutation.defaults import (
    build_policy_defaults_from_details,
    build_runtime_defaults_from_details,
    resolve_current_runtime_config,
)
from afkbot.cli.presentation.setup_prompts import PromptLanguage
from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.services.profile_runtime import ProfileDetails
from afkbot.services.setup.contracts import PolicySetupMode
from afkbot.services.setup.policy_inputs import (
    has_explicit_policy_overrides,
    resolve_policy_setup_mode,
)
from afkbot.services.setup.profile_resolution import (
    ResolvedProfilePolicyInputs,
    ResolvedProfileRuntimeCore,
    build_profile_runtime_config,
    resolve_profile_policy_inputs,
    resolve_profile_runtime_core,
)
from afkbot.services.setup.provider_secrets import (
    ResolvedProviderApiKeyInput,
    resolve_profile_provider_api_key,
)
from afkbot.settings import Settings


def collect_profile_mutation_inputs(
    *,
    settings: Settings,
    interactive: bool,
    lang: PromptLanguage,
    profile_id: str,
    current_details: ProfileDetails | None,
    defaults: dict[str, str],
    name: str | None,
    llm_provider: str | None,
    chat_model: str | None,
    thinking_level: str | None,
    llm_base_url: str | None,
    custom_interface: str | None,
    llm_proxy_type: str | None,
    llm_proxy_url: str | None,
    llm_api_key_file: Path | None,
    llm_api_key: str | None,
    provider_api_key: str | None,
    minimax_region: str | None,
    planning_mode: str | None,
    chat_secret_guard_enabled: bool | None,
    current_runtime_secrets: dict[str, str] | None,
    policy_enabled: bool | None,
    policy_preset: str | None,
    policy_capability: tuple[str, ...],
    policy_file_access_mode: str | None,
    policy_workspace_scope: str | None,
    policy_allowed_dir: tuple[str, ...],
    policy_network_host: tuple[str, ...],
    llm_history_turns: int | None,
    tool_plugins: tuple[str, ...],
    memory_auto_search_enabled: bool | None,
    memory_auto_search_scope_mode: str | None,
    memory_auto_search_limit: int | None,
    memory_auto_search_include_global: bool | None,
    memory_auto_search_chat_limit: int | None,
    memory_auto_search_global_limit: int | None,
    memory_global_fallback_enabled: bool | None,
    memory_auto_context_item_chars: int | None,
    memory_auto_save_enabled: bool | None,
    memory_auto_save_scope_mode: str | None,
    memory_auto_promote_enabled: bool | None,
    memory_auto_save_kinds: tuple[str, ...],
    memory_auto_save_max_chars: int | None,
    session_compaction_enabled: bool | None,
    session_compaction_trigger_turns: int | None,
    session_compaction_keep_recent_turns: int | None,
    session_compaction_max_chars: int | None,
    session_compaction_prune_raw_turns: bool | None,
) -> CollectedProfileMutationInputs:
    """Resolve shared create/update inputs with one consistent merge order."""

    base_runtime = resolve_current_runtime_config(current_details) if current_details is not None else None
    runtime_defaults = (
        build_runtime_defaults_from_details(current_details)
        if current_details is not None
        else defaults
    )
    policy_defaults = (
        build_policy_defaults_from_details(root_dir=settings.root_dir, details=current_details)
        if current_details is not None
        else defaults
    )
    resolved_name = resolve_profile_name(
        value=name,
        interactive=interactive,
        default=current_details.name if current_details is not None else profile_id,
        lang=lang,
    )
    resolved_provider_key_input: ResolvedProviderApiKeyInput | None = None

    def _resolve_provider_token(provider_id: LLMProviderId, provider_name: str) -> str | None:
        nonlocal resolved_provider_key_input
        resolved_provider_key_input = resolve_profile_provider_api_key(
            provider_id=provider_id,
            provider_name=provider_name,
            interactive=interactive,
            defaults=runtime_defaults,
            lang=lang,
            key_file=llm_api_key_file,
            current_runtime_secrets=current_runtime_secrets,
            generic_api_key=llm_api_key,
            provider_api_key=provider_api_key,
            minimax_region=minimax_region,
            required=interactive,
        )
        return resolved_provider_key_input.preferred_base_url

    runtime_core = resolve_profile_runtime_core(
        interactive=interactive,
        lang=lang,
        settings=settings,
        provider_value=llm_provider,
        model_value=chat_model,
        thinking_level_value=thinking_level,
        thinking_level_prompt=(
            "Default thinking level (low, medium, high, very_high)"
            if lang == "en"
            else "Уровень раздумия по умолчанию (low, medium, high, very_high)"
        ),
        base_url_value=llm_base_url,
        base_url_prompt=(
            "Profile provider base URL" if lang == "en" else "Base URL провайдера профиля"
        ),
        custom_interface_value=custom_interface,
        proxy_type_value=llm_proxy_type,
        proxy_url_value=llm_proxy_url,
        planning_mode_value=planning_mode,
        default_provider=runtime_defaults.get("AFKBOT_LLM_PROVIDER", settings.llm_provider),
        default_model=runtime_defaults.get("AFKBOT_LLM_MODEL", settings.llm_model),
        default_thinking_level=runtime_defaults.get(
            "AFKBOT_LLM_THINKING_LEVEL",
            settings.llm_thinking_level,
        ),
        default_base_url=runtime_defaults.get("AFKBOT_LLM_BASE_URL"),
        default_custom_interface=runtime_defaults.get(
            "AFKBOT_CUSTOM_INTERFACE",
            settings.custom_interface,
        ),
        default_proxy_type=runtime_defaults.get(
            "AFKBOT_LLM_PROXY_TYPE",
            settings.llm_proxy_type,
        ),
        default_proxy_url=runtime_defaults.get(
            "AFKBOT_LLM_PROXY_URL",
            settings.llm_proxy_url or "",
        ),
        default_planning_mode=base_runtime.chat_planning_mode if base_runtime is not None else None,
        provider_api_key_hook=_resolve_provider_token,
    )
    if resolved_provider_key_input is None:
        resolved_provider_key_input = resolve_profile_provider_api_key(
            provider_id=runtime_core.provider_id,
            provider_name=runtime_core.llm_provider,
            interactive=interactive,
            defaults=runtime_defaults,
            lang=lang,
            key_file=llm_api_key_file,
            current_runtime_secrets=current_runtime_secrets,
            generic_api_key=llm_api_key,
            provider_api_key=provider_api_key,
            minimax_region=minimax_region,
            required=interactive,
        )
    explicit_policy_overrides = has_explicit_policy_overrides(
        policy_enabled=policy_enabled,
        policy_preset=policy_preset,
        policy_capability=policy_capability,
        policy_file_access_mode=policy_file_access_mode,
        policy_workspace_scope=policy_workspace_scope,
        policy_network_host=policy_network_host,
    )
    policy_setup_mode = resolve_policy_setup_mode(
        interactive=interactive,
        defaults=policy_defaults,
        explicit_policy_overrides=explicit_policy_overrides,
        lang=lang,
    )
    resolved_policy = resolve_profile_policy_inputs(
        interactive=interactive
        and not (
            policy_setup_mode == PolicySetupMode.RECOMMENDED.value
            and not explicit_policy_overrides
        ),
        lang=lang,
        root_dir=settings.root_dir,
        profile_root=settings.profiles_dir / profile_id,
        defaults=policy_defaults,
        current_allowed_directories=(
            current_details.policy.allowed_directories if current_details is not None else ()
        ),
        policy_enabled_value=policy_enabled,
        policy_preset_value=policy_preset,
        policy_capability_values=policy_capability,
        policy_file_access_mode_value=policy_file_access_mode,
        policy_workspace_scope_value=policy_workspace_scope,
        policy_allowed_dir_values=policy_allowed_dir,
        policy_network_host_values=policy_network_host,
        allow_custom_workspace_scope=(
            str(policy_defaults.get("AFKBOT_POLICY_WORKSPACE_SCOPE", "profile_only")) == "custom"
        ),
    )
    runtime_config = build_profile_runtime_config(
        base_runtime=base_runtime,
        runtime_core=runtime_core,
        llm_history_turns=llm_history_turns,
        chat_secret_guard_enabled=chat_secret_guard_enabled,
        tool_plugins=tool_plugins,
        memory_auto_search_enabled=memory_auto_search_enabled,
        memory_auto_search_scope_mode=memory_auto_search_scope_mode,
        memory_auto_search_limit=memory_auto_search_limit,
        memory_auto_search_include_global=memory_auto_search_include_global,
        memory_auto_search_chat_limit=memory_auto_search_chat_limit,
        memory_auto_search_global_limit=memory_auto_search_global_limit,
        memory_global_fallback_enabled=memory_global_fallback_enabled,
        memory_auto_context_item_chars=memory_auto_context_item_chars,
        memory_auto_save_enabled=memory_auto_save_enabled,
        memory_auto_save_scope_mode=memory_auto_save_scope_mode,
        memory_auto_promote_enabled=memory_auto_promote_enabled,
        memory_auto_save_kinds=memory_auto_save_kinds,
        memory_auto_save_max_chars=memory_auto_save_max_chars,
        session_compaction_enabled=session_compaction_enabled,
        session_compaction_trigger_turns=session_compaction_trigger_turns,
        session_compaction_keep_recent_turns=session_compaction_keep_recent_turns,
        session_compaction_max_chars=session_compaction_max_chars,
        session_compaction_prune_raw_turns=session_compaction_prune_raw_turns,
    )
    return CollectedProfileMutationInputs(
        resolved_name=resolved_name,
        runtime_core=runtime_core,
        provider_api_key=resolved_provider_key_input.effective_api_key,
        runtime_secrets_update=resolved_provider_key_input.runtime_secrets_update,
        resolved_policy=resolved_policy,
        runtime_config=runtime_config,
    )


def collect_setup_profile_inputs(
    *,
    settings: Settings,
    interactive: bool,
    lang: PromptLanguage,
    current_details: ProfileDetails | None,
    current_runtime_secrets: dict[str, str] | None,
    defaults: dict[str, str],
    llm_provider: str | None,
    chat_model: str | None,
    thinking_level: str | None,
    llm_base_url: str | None,
    custom_interface: str | None,
    llm_proxy_type: str | None,
    llm_proxy_url: str | None,
    llm_api_key_file: Path | None,
    minimax_region: str | None,
    policy_enabled: bool | None,
    policy_preset: str | None,
    policy_capability: tuple[str, ...],
    policy_file_access_mode: str | None,
    policy_workspace_scope: str | None,
    policy_network_host: tuple[str, ...],
) -> CollectedProfileMutationInputs:
    """Resolve the setup-facing subset of default-profile mutation inputs."""

    return collect_profile_mutation_inputs(
        settings=settings,
        interactive=interactive,
        lang=lang,
        profile_id="default",
        current_details=current_details,
        defaults=defaults,
        name=current_details.name if current_details is not None else "Default",
        llm_provider=llm_provider,
        chat_model=chat_model,
        thinking_level=thinking_level,
        llm_base_url=llm_base_url,
        custom_interface=custom_interface,
        llm_proxy_type=llm_proxy_type,
        llm_proxy_url=llm_proxy_url,
        llm_api_key_file=llm_api_key_file,
        llm_api_key=None,
        provider_api_key=None,
        minimax_region=minimax_region,
        planning_mode=None,
        chat_secret_guard_enabled=None,
        current_runtime_secrets=current_runtime_secrets,
        policy_enabled=policy_enabled,
        policy_preset=policy_preset,
        policy_capability=policy_capability,
        policy_file_access_mode=policy_file_access_mode,
        policy_workspace_scope=policy_workspace_scope,
        policy_allowed_dir=(),
        policy_network_host=policy_network_host,
        llm_history_turns=None,
        tool_plugins=(),
        memory_auto_search_enabled=None,
        memory_auto_search_scope_mode=None,
        memory_auto_search_limit=None,
        memory_auto_search_include_global=None,
        memory_auto_search_chat_limit=None,
        memory_auto_search_global_limit=None,
        memory_global_fallback_enabled=None,
        memory_auto_context_item_chars=None,
        memory_auto_save_enabled=None,
        memory_auto_save_scope_mode=None,
        memory_auto_promote_enabled=None,
        memory_auto_save_kinds=(),
        memory_auto_save_max_chars=None,
        session_compaction_enabled=None,
        session_compaction_trigger_turns=None,
        session_compaction_keep_recent_turns=None,
        session_compaction_max_chars=None,
        session_compaction_prune_raw_turns=None,
    )


__all__ = [
    "CollectedProfileMutationInputs",
    "ResolvedProfilePolicyInputs",
    "ResolvedProfileRuntimeCore",
    "collect_profile_mutation_inputs",
    "collect_setup_profile_inputs",
]
