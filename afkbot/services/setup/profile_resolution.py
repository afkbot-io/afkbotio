"""Shared runtime/policy resolution for setup and profile mutation flows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

import typer

from afkbot.services.agent_loop.planning_policy import normalize_chat_planning_mode
from afkbot.cli.presentation.setup_prompts import PromptLanguage
from afkbot.services.setup.policy_inputs import (
    default_policy_enabled_for_preset,
    resolve_policy_capabilities,
    resolve_policy_enabled,
    resolve_policy_file_access_mode,
    resolve_policy_network_settings,
    resolve_policy_preset,
    resolve_policy_workspace_scope_mode,
)
from afkbot.services.setup.provider_inputs import (
    resolve_custom_interface,
    resolve_model,
    resolve_provider,
    resolve_provider_base_url_default,
    resolve_proxy,
    resolve_thinking_level,
    resolve_text,
)
from afkbot.services.llm.provider_catalog import LLMProviderId, parse_provider
from afkbot.services.llm.reasoning import normalize_thinking_level
from afkbot.services.policy import (
    normalize_workspace_scope_mode,
    resolve_allowed_directories_for_scope_mode,
)
from afkbot.services.policy.evaluation_helpers import normalize_path
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import Settings


@dataclass(frozen=True)
class ResolvedProfileRuntimeCore:
    """Resolved provider/runtime fields shared by setup and profile mutation."""

    provider_id: LLMProviderId
    llm_provider: str
    llm_model: str
    llm_base_url: str
    custom_interface: str
    llm_proxy_type: str
    llm_proxy_url: str
    llm_thinking_level: str | None
    chat_planning_mode: str | None


@dataclass(frozen=True)
class ResolvedProfilePolicyInputs:
    """Resolved policy overlay shared by setup and profile mutation."""

    enabled: bool
    preset: str
    capabilities: tuple[str, ...]
    file_access_mode: str
    workspace_scope_mode: str
    allowed_directories: tuple[str, ...]
    network_mode: str
    network_allowlist: tuple[str, ...]


def resolve_profile_thinking_level(
    *,
    value: str | None,
    interactive: bool,
    default: str | None,
    lang: PromptLanguage,
    prompt: str,
) -> str | None:
    """Resolve one thinking level with optional interactive prompt."""

    del prompt
    normalized = resolve_thinking_level(
        value=value,
        interactive=interactive,
        default=default or "medium",
        lang=lang,
    )
    return normalize_thinking_level(normalized)


def resolve_profile_runtime_core(
    *,
    interactive: bool,
    lang: PromptLanguage,
    settings: Settings,
    provider_value: str | None,
    model_value: str | None,
    thinking_level_value: str | None,
    thinking_level_prompt: str,
    base_url_value: str | None,
    base_url_prompt: str,
    custom_interface_value: str | None,
    proxy_type_value: str | None,
    proxy_url_value: str | None,
    planning_mode_value: str | None,
    default_provider: str,
    default_model: str,
    default_thinking_level: str | None,
    default_base_url: str | None,
    default_custom_interface: str,
    default_proxy_type: str,
    default_proxy_url: str,
    default_planning_mode: str | None,
    provider_api_key_hook: Callable[[LLMProviderId, str], str | None] | None = None,
) -> ResolvedProfileRuntimeCore:
    """Resolve provider/model/runtime inputs with identical create/update semantics."""

    resolved_provider = resolve_provider(
        value=provider_value,
        interactive=interactive,
        default=default_provider,
        lang=lang,
    )
    provider_id = parse_provider(resolved_provider)
    resolved_model = resolve_model(
        provider=resolved_provider,
        value=model_value,
        interactive=interactive,
        default=default_model,
        lang=lang,
    )
    provider_default_base_url_override: str | None = None
    if provider_api_key_hook is not None:
        provider_default_base_url_override = provider_api_key_hook(provider_id, resolved_provider)
    resolved_thinking_level = resolve_profile_thinking_level(
        value=thinking_level_value,
        interactive=interactive,
        default=default_thinking_level,
        lang=lang,
        prompt=thinking_level_prompt,
    )
    resolved_custom_interface = default_custom_interface
    if provider_id == LLMProviderId.CUSTOM:
        resolved_custom_interface = resolve_custom_interface(
            value=custom_interface_value,
            interactive=interactive,
            default=default_custom_interface,
            lang=lang,
        )
    provider_default_base_url = provider_default_base_url_override or resolve_provider_base_url_default(
        defaults={},
        settings=settings,
        provider_id=provider_id,
    )
    provider_changed = resolved_provider.strip().lower() != default_provider.strip().lower()
    if provider_changed and base_url_value is None:
        # When provider changes, prefer the new provider default instead of carrying stale URL.
        effective_default_base_url = provider_default_base_url
    else:
        effective_default_base_url = default_base_url or provider_default_base_url
    should_prompt_base_url = interactive and (
        base_url_value is not None
        or provider_id == LLMProviderId.CUSTOM
        or effective_default_base_url.strip() != provider_default_base_url.strip()
    )
    resolved_base_url = resolve_text(
        value=base_url_value,
        interactive=should_prompt_base_url,
        prompt=base_url_prompt,
        default=effective_default_base_url,
        lang=lang,
    )
    resolved_proxy_type, resolved_proxy_url = resolve_proxy(
        interactive=interactive,
        value_type=proxy_type_value,
        value_url=proxy_url_value,
        default_type=default_proxy_type,
        default_url=default_proxy_url,
        lang=lang,
    )
    resolved_planning_mode = (
        normalize_chat_planning_mode(planning_mode_value)
        if planning_mode_value is not None
        else default_planning_mode
    )
    return ResolvedProfileRuntimeCore(
        provider_id=provider_id,
        llm_provider=resolved_provider,
        llm_model=resolved_model,
        llm_base_url=resolved_base_url,
        custom_interface=resolved_custom_interface,
        llm_proxy_type=resolved_proxy_type,
        llm_proxy_url=resolved_proxy_url,
        llm_thinking_level=resolved_thinking_level,
        chat_planning_mode=resolved_planning_mode,
    )


def build_profile_runtime_config(
    *,
    base_runtime: ProfileRuntimeConfig | None,
    runtime_core: ResolvedProfileRuntimeCore,
    llm_history_turns: int | None,
    chat_secret_guard_enabled: bool | None,
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
) -> ProfileRuntimeConfig:
    """Build one runtime config by overlaying explicit flags onto an optional baseline."""

    current = base_runtime
    return ProfileRuntimeConfig(
        llm_provider=runtime_core.llm_provider,
        llm_model=runtime_core.llm_model,
        llm_base_url=runtime_core.llm_base_url,
        custom_interface=runtime_core.custom_interface,
        llm_proxy_type=runtime_core.llm_proxy_type,
        llm_proxy_url=runtime_core.llm_proxy_url,
        llm_thinking_level=runtime_core.llm_thinking_level,
        llm_history_turns=(
            llm_history_turns if llm_history_turns is not None else (current.llm_history_turns if current else None)
        ),
        chat_planning_mode=runtime_core.chat_planning_mode,
        chat_secret_guard_enabled=(
            chat_secret_guard_enabled
            if chat_secret_guard_enabled is not None
            else (current.chat_secret_guard_enabled if current else None)
        ),
        enabled_tool_plugins=tool_plugins if tool_plugins else (current.enabled_tool_plugins if current else None),
        memory_auto_search_enabled=(
            memory_auto_search_enabled
            if memory_auto_search_enabled is not None
            else (current.memory_auto_search_enabled if current else None)
        ),
        memory_auto_search_scope_mode=(
            memory_auto_search_scope_mode
            if memory_auto_search_scope_mode is not None
            else (current.memory_auto_search_scope_mode if current else None)
        ),
        memory_auto_search_limit=(
            memory_auto_search_limit
            if memory_auto_search_limit is not None
            else (current.memory_auto_search_limit if current else None)
        ),
        memory_auto_search_include_global=(
            memory_auto_search_include_global
            if memory_auto_search_include_global is not None
            else (current.memory_auto_search_include_global if current else None)
        ),
        memory_auto_search_chat_limit=(
            memory_auto_search_chat_limit
            if memory_auto_search_chat_limit is not None
            else (current.memory_auto_search_chat_limit if current else None)
        ),
        memory_auto_search_global_limit=(
            memory_auto_search_global_limit
            if memory_auto_search_global_limit is not None
            else (current.memory_auto_search_global_limit if current else None)
        ),
        memory_global_fallback_enabled=(
            memory_global_fallback_enabled
            if memory_global_fallback_enabled is not None
            else (current.memory_global_fallback_enabled if current else None)
        ),
        memory_auto_context_item_chars=(
            memory_auto_context_item_chars
            if memory_auto_context_item_chars is not None
            else (current.memory_auto_context_item_chars if current else None)
        ),
        memory_auto_save_enabled=(
            memory_auto_save_enabled
            if memory_auto_save_enabled is not None
            else (current.memory_auto_save_enabled if current else None)
        ),
        memory_auto_save_scope_mode=(
            memory_auto_save_scope_mode
            if memory_auto_save_scope_mode is not None
            else (current.memory_auto_save_scope_mode if current else None)
        ),
        memory_auto_promote_enabled=(
            memory_auto_promote_enabled
            if memory_auto_promote_enabled is not None
            else (current.memory_auto_promote_enabled if current else None)
        ),
        memory_auto_save_kinds=(
            memory_auto_save_kinds if memory_auto_save_kinds else (current.memory_auto_save_kinds if current else None)
        ),
        memory_auto_save_max_chars=(
            memory_auto_save_max_chars
            if memory_auto_save_max_chars is not None
            else (current.memory_auto_save_max_chars if current else None)
        ),
        session_compaction_enabled=(
            session_compaction_enabled
            if session_compaction_enabled is not None
            else (current.session_compaction_enabled if current else None)
        ),
        session_compaction_trigger_turns=(
            session_compaction_trigger_turns
            if session_compaction_trigger_turns is not None
            else (current.session_compaction_trigger_turns if current else None)
        ),
        session_compaction_keep_recent_turns=(
            session_compaction_keep_recent_turns
            if session_compaction_keep_recent_turns is not None
            else (current.session_compaction_keep_recent_turns if current else None)
        ),
        session_compaction_max_chars=(
            session_compaction_max_chars
            if session_compaction_max_chars is not None
            else (current.session_compaction_max_chars if current else None)
        ),
        session_compaction_prune_raw_turns=(
            session_compaction_prune_raw_turns
            if session_compaction_prune_raw_turns is not None
            else (current.session_compaction_prune_raw_turns if current else None)
        ),
    )


def resolve_policy_allowed_directories(
    *,
    root_dir: Path,
    raw_values: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve repeatable allowed directories into stable absolute paths."""

    if not raw_values:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        value = raw.strip()
        if not value:
            continue
        path = str(normalize_path(root_dir=root_dir, raw=value))
        if path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return tuple(normalized)


def normalize_policy_file_access_mode_value(value: str) -> str:
    """Validate one explicit profile file-access mode flag value."""

    normalized = value.strip().lower()
    if normalized not in {"none", "read_only", "read_write"}:
        raise typer.BadParameter("policy file access mode must be one of: none, read_only, read_write")
    return normalized


def normalize_policy_workspace_scope_mode_value(value: str) -> str:
    """Validate one explicit profile workspace-scope flag value."""

    try:
        return normalize_workspace_scope_mode(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def resolve_policy_allowed_directories_from_scope(
    *,
    root_dir: Path,
    profile_root: Path,
    workspace_scope_mode: str,
    raw_values: tuple[str, ...],
    current_allowed_directories: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Resolve final allowed directories from declarative scope mode plus optional custom paths."""

    normalized_scope_mode = normalize_policy_workspace_scope_mode_value(workspace_scope_mode)
    custom_allowed_directories = (
        resolve_policy_allowed_directories(
            root_dir=root_dir,
            raw_values=raw_values,
        )
        if raw_values
        else tuple(current_allowed_directories)
    )
    if normalized_scope_mode == "custom":
        if not custom_allowed_directories:
            raise typer.BadParameter("custom workspace scope requires --policy-allowed-dir on create/update")
        return resolve_allowed_directories_for_scope_mode(
            root_dir=root_dir,
            profile_root=profile_root,
            scope_mode=normalized_scope_mode,
            custom_allowed_directories=custom_allowed_directories,
        )
    return resolve_allowed_directories_for_scope_mode(
        root_dir=root_dir,
        profile_root=profile_root,
        scope_mode=normalized_scope_mode,
    )


def resolve_profile_policy_inputs(
    *,
    interactive: bool,
    lang: PromptLanguage,
    root_dir: Path,
    profile_root: Path,
    defaults: dict[str, str],
    current_allowed_directories: tuple[str, ...] = (),
    policy_enabled_value: bool | None,
    policy_preset_value: str | None,
    policy_capability_values: tuple[str, ...],
    policy_file_access_mode_value: str | None,
    policy_workspace_scope_value: str | None,
    policy_allowed_dir_values: tuple[str, ...],
    policy_network_host_values: tuple[str, ...],
    allow_custom_workspace_scope: bool = False,
) -> ResolvedProfilePolicyInputs:
    """Resolve one full profile policy payload with shared create/update semantics."""

    resolved_policy_preset = resolve_policy_preset(
        value=policy_preset_value,
        interactive=interactive,
        default=str(defaults.get("AFKBOT_POLICY_PRESET", "medium")),
        lang=lang,
    )
    resolved_policy_enabled = resolve_policy_enabled(
        value=policy_enabled_value,
        interactive=interactive,
        default=default_policy_enabled_for_preset(
            defaults=defaults,
            preset=resolved_policy_preset,
        ),
        lang=lang,
    )
    policy_details_interactive = interactive and resolved_policy_enabled
    resolved_policy_capabilities = resolve_policy_capabilities(
        value=policy_capability_values,
        interactive=policy_details_interactive,
        preset=resolved_policy_preset,
        defaults=defaults,
        lang=lang,
    )
    resolved_policy_file_access_mode = (
        normalize_policy_file_access_mode_value(policy_file_access_mode_value)
        if policy_file_access_mode_value is not None
        else resolve_policy_file_access_mode(
            value=None,
            interactive=policy_details_interactive,
            defaults=defaults,
            lang=lang,
        )
    )
    current_workspace_scope_default = str(defaults.get("AFKBOT_POLICY_WORKSPACE_SCOPE", "profile_only"))
    if "files" not in set(resolved_policy_capabilities) or resolved_policy_file_access_mode == "none":
        resolved_policy_workspace_scope = "profile_only"
    elif policy_workspace_scope_value is not None:
        resolved_policy_workspace_scope = normalize_policy_workspace_scope_mode_value(
            policy_workspace_scope_value
        )
    elif policy_details_interactive and not policy_allowed_dir_values:
        resolved_policy_workspace_scope = resolve_policy_workspace_scope_mode(
            value=None,
            interactive=True,
            default=current_workspace_scope_default,
            lang=lang,
            allow_custom=allow_custom_workspace_scope,
        )
    else:
        resolved_policy_workspace_scope = current_workspace_scope_default
    resolved_policy_allowed_directories = resolve_policy_allowed_directories_from_scope(
        root_dir=root_dir,
        profile_root=profile_root,
        workspace_scope_mode=resolved_policy_workspace_scope,
        raw_values=policy_allowed_dir_values,
        current_allowed_directories=current_allowed_directories,
    )
    resolved_network_mode, resolved_network_allowlist = resolve_policy_network_settings(
        value=policy_network_host_values,
        interactive=policy_details_interactive,
        defaults=defaults,
        capabilities=resolved_policy_capabilities,
        lang=lang,
    )
    return ResolvedProfilePolicyInputs(
        enabled=resolved_policy_enabled,
        preset=resolved_policy_preset,
        capabilities=resolved_policy_capabilities,
        file_access_mode=resolved_policy_file_access_mode,
        workspace_scope_mode=resolved_policy_workspace_scope,
        allowed_directories=resolved_policy_allowed_directories,
        network_mode=resolved_network_mode,
        network_allowlist=resolved_network_allowlist,
    )
