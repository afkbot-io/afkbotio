"""Shared inspection helpers for profile/channel CLI views."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.agent_loop.sensitive_tool_policy import blocked_tool_names_for_runtime
from afkbot.services.channel_routing.policy import is_user_facing_transport
from afkbot.services.channels.endpoint_contracts import (
    ChannelEndpointConfig,
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
)
from afkbot.services.channels.tool_profiles import (
    ChannelToolProfile,
    allowed_tool_names_for_channel_profile,
)
from afkbot.services.policy.presets_catalog import CAPABILITY_ORDER
from afkbot.services.policy import infer_workspace_scope_mode
from afkbot.services.profile_runtime.contracts import (
    ProfileDetails,
    ProfilePolicyView,
    ProfileRuntimeResolved,
)
from afkbot.settings import Settings
from afkbot.cli.commands.inspection_shared_models import (
    ChannelGuardrailSummary,
    ChannelInspectionSummary,
    EffectivePermissionSummary,
    LinkedChannelInspectionSummary,
    LinkedChannelSummary,
    MemoryBehaviorSummary,
    MutationStateSummary,
    ToolAccessSummary,
)

__all__ = [
    "ChannelGuardrailSummary",
    "ChannelInspectionSummary",
    "EffectivePermissionSummary",
    "LinkedChannelInspectionSummary",
    "LinkedChannelSummary",
    "MemoryBehaviorSummary",
    "MutationStateSummary",
    "ToolAccessSummary",
    "build_channel_guardrail_summary",
    "build_channel_inspection_summary",
    "build_linked_channel_inspection_summary",
    "build_linked_channel_summary",
    "build_profile_mutation_state_summary",
    "build_profile_permission_summary",
    "render_memory_auto_save_brief",
    "render_memory_auto_search_brief",
    "render_merge_order_brief",
    "render_profile_memory_defaults_brief",
    "render_tool_access_brief",
]


def render_merge_order_brief() -> str:
    """Render the canonical mutation merge order for human-facing CLI output."""

    return "explicit > current > inherited > system"


def render_tool_access_brief(tool_access: ToolAccessSummary) -> str:
    """Render one compact tool-access summary for human-facing CLI output."""

    return ", ".join(
        (
            f"files={tool_access.files}",
            f"shell={tool_access.shell}",
            f"memory={tool_access.memory}",
            f"credentials={tool_access.credentials}",
            f"apps={tool_access.apps}",
        )
    )


def render_memory_auto_search_brief(memory_behavior: "MemoryBehaviorSummary") -> str:
    """Render one compact automatic memory-search summary for CLI output."""

    if not memory_behavior.auto_search_enabled:
        return "off"
    return (
        "on("
        f"scope={memory_behavior.auto_search_scope_mode},"
        f"chat_limit={memory_behavior.auto_search_chat_limit},"
        f"global_limit={memory_behavior.auto_search_global_limit},"
        f"include_global={memory_behavior.auto_search_include_global}"
        ")"
    )


def render_memory_auto_save_brief(memory_behavior: "MemoryBehaviorSummary") -> str:
    """Render one compact automatic memory-save summary for CLI output."""

    if not memory_behavior.auto_save_enabled:
        return "off"
    return (
        "on("
        f"scope={memory_behavior.auto_save_scope_mode},"
        "kinds="
        + ",".join(memory_behavior.auto_save_kinds)
        + ")"
    )


def render_profile_memory_defaults_brief(memory_behavior: "MemoryBehaviorSummary") -> str:
    """Render inherited profile-memory defaults for one channel inspection summary."""

    search_part = (
        "off"
        if not memory_behavior.auto_search_enabled
        else (
            "search("
            f"scope={memory_behavior.auto_search_scope_mode},"
            f"chat_limit={memory_behavior.auto_search_chat_limit},"
            f"global_limit={memory_behavior.auto_search_global_limit},"
            f"include_global={memory_behavior.auto_search_include_global}"
            ")"
        )
    )
    save_part = (
        "save=off"
        if not memory_behavior.auto_save_enabled
        else (
            "save("
            f"scope={memory_behavior.auto_save_scope_mode},"
            "kinds="
            + ",".join(memory_behavior.auto_save_kinds)
            + ")"
        )
    )
    return f"{search_part}; {save_part}"


def build_profile_permission_summary(
    *,
    settings: Settings,
    profile: ProfileDetails,
) -> EffectivePermissionSummary:
    """Build one operator-facing permission summary from persisted profile policy."""

    return _build_permission_summary(
        settings=settings,
        policy=profile.policy,
        runtime=profile.effective_runtime,
        profile_root=Path(profile.profile_root),
        transport=None,
    )


def build_profile_mutation_state_summary(profile: ProfileDetails) -> MutationStateSummary:
    """Summarize how one profile resolves persisted overrides and inherited defaults."""

    current_runtime_overrides = _profile_current_runtime_overrides(profile)
    current_policy_overrides = _profile_current_policy_overrides(profile)
    current_overrides = dict(current_runtime_overrides)
    if current_policy_overrides:
        current_overrides["policy"] = current_policy_overrides
    return MutationStateSummary(
        merge_order=_mutation_merge_order(),
        inherited_defaults_source="global runtime settings and setup defaults",
        update_preserves_unspecified=True,
        current_override_fields=tuple(
            sorted(
                (
                    *(f"runtime.{key}" for key in current_runtime_overrides),
                    *(f"policy.{key}" for key in current_policy_overrides),
                )
            )
        ),
        current_overrides=current_overrides,
    )


def build_channel_inspection_summary(
    *,
    settings: Settings,
    profile: ProfileDetails,
    channel: ChannelEndpointConfig,
) -> ChannelInspectionSummary:
    """Build one channel-scoped inspection summary from profile ceiling + transport guards."""

    blocked_tools = tuple(
        sorted(
            blocked_tool_names_for_runtime(
                runtime_metadata={"transport": channel.transport},
            )
        )
    )
    return ChannelInspectionSummary(
        profile_id=profile.id,
        transport=channel.transport,
        user_facing_transport=is_user_facing_transport(channel.transport),
        channel_tool_profile=channel.tool_profile,
        channel_tool_profile_allowlist=tuple(
            allowed_tool_names_for_channel_profile(channel.tool_profile) or ()
        ),
        hard_blocked_tools=blocked_tools,
        mutation_state=build_channel_mutation_state_summary(profile=profile, channel=channel),
        profile_ceiling=build_profile_permission_summary(
            settings=settings,
            profile=profile,
        ),
        effective_permissions=_build_permission_summary(
            settings=settings,
            policy=profile.policy,
            runtime=profile.effective_runtime,
            profile_root=Path(profile.profile_root),
            transport=channel.transport,
            channel_tool_profile=channel.tool_profile,
        ),
    )


def build_linked_channel_inspection_summary(
    *,
    settings: Settings,
    profile: ProfileDetails,
    channel: ChannelEndpointConfig,
) -> LinkedChannelInspectionSummary:
    """Build one profile-linked channel summary with applied narrowing details."""

    inspection = build_channel_inspection_summary(
        settings=settings,
        profile=profile,
        channel=channel,
    )
    return LinkedChannelInspectionSummary(
        channel=build_linked_channel_summary(channel),
        mutation_state=inspection.mutation_state,
        channel_guardrails=build_channel_guardrail_summary(inspection),
        profile_ceiling=inspection.profile_ceiling,
        effective_permissions=inspection.effective_permissions,
    )


def build_channel_mutation_state_summary(
    *,
    profile: ProfileDetails,
    channel: ChannelEndpointConfig,
) -> MutationStateSummary:
    """Summarize how one channel resolves current config and inherited profile defaults."""

    current_overrides = _channel_current_overrides(channel)
    return MutationStateSummary(
        merge_order=_mutation_merge_order(),
        inherited_defaults_source=f"profile:{profile.id}",
        update_preserves_unspecified=True,
        current_override_fields=tuple(sorted(current_overrides)),
        current_overrides=current_overrides,
        narrowing_behavior="channel overlay may narrow profile permissions only",
    )


def build_linked_channel_summary(channel: ChannelEndpointConfig) -> LinkedChannelSummary:
    """Build a concise per-channel summary suitable for profile inspection output."""

    mode = "default"
    if isinstance(channel, TelegramPollingEndpointConfig):
        mode = channel.group_trigger_mode
    elif isinstance(channel, TelethonUserEndpointConfig):
        mode = channel.reply_mode
    return LinkedChannelSummary(
        endpoint_id=channel.endpoint_id,
        transport=channel.transport,
        adapter_kind=channel.adapter_kind,
        account_id=channel.account_id,
        enabled=channel.enabled,
        mode=mode,
    )


def build_channel_guardrail_summary(
    inspection: ChannelInspectionSummary,
) -> ChannelGuardrailSummary:
    """Project one full channel inspection into reusable guardrail-only payload."""

    return ChannelGuardrailSummary(
        user_facing_transport=inspection.user_facing_transport,
        channel_tool_profile=inspection.channel_tool_profile,
        channel_tool_profile_allowlist=inspection.channel_tool_profile_allowlist,
        hard_blocked_tools=inspection.hard_blocked_tools,
    )


def _build_permission_summary(
    *,
    settings: Settings,
    policy: ProfilePolicyView,
    runtime: ProfileRuntimeResolved,
    profile_root: Path,
    transport: str | None,
    channel_tool_profile: ChannelToolProfile | None = None,
) -> EffectivePermissionSummary:
    capabilities = _effective_capability_ids(policy=policy)
    tool_access = _build_tool_access_summary(
        policy=policy,
        capabilities=capabilities,
        transport=transport,
        channel_tool_profile=channel_tool_profile,
    )
    return EffectivePermissionSummary(
        policy_enabled=policy.enabled,
        policy_preset=policy.preset,
        capability_ids=capabilities,
        default_workspace_root=_display_path(settings=settings, path=profile_root),
        shell_default_cwd=_display_path(settings=settings, path=profile_root),
        file_scope_mode=_infer_file_scope_mode(
            settings=settings,
            policy=policy,
            profile_root=profile_root,
        ),
        file_access_mode=tool_access.files,
        network_access=_infer_network_access(policy=policy),
        network_allowlist=tuple(
            _display_path(settings=settings, path=Path(item))
            if item.startswith("/")
            else item
            for item in policy.network_allowlist
        ),
        memory_behavior=_build_memory_behavior_summary(
            policy=policy,
            runtime=runtime,
            transport=transport,
        ),
        tool_access=tool_access,
    )


def _effective_capability_ids(*, policy: ProfilePolicyView) -> tuple[str, ...]:
    if policy.enabled:
        return tuple(policy.capabilities)
    return tuple(item.value for item in CAPABILITY_ORDER)


def _build_tool_access_summary(
    *,
    policy: ProfilePolicyView,
    capabilities: tuple[str, ...],
    transport: str | None,
    channel_tool_profile: ChannelToolProfile | None = None,
) -> ToolAccessSummary:
    capability_set = set(capabilities)
    if not policy.enabled:
        file_access = "full_system"
    elif "files" in capability_set:
        file_access = policy.file_access_mode
    else:
        file_access = "disabled"
    credentials_access = _enabled_mode(enabled=(not policy.enabled) or ("credentials" in capability_set))
    if transport is not None and is_user_facing_transport(transport):
        credentials_access = "blocked_in_user_channel"
    summary = ToolAccessSummary(
        files=file_access,
        shell=_enabled_mode(enabled=(not policy.enabled) or ("shell" in capability_set)),
        memory=_enabled_mode(enabled=(not policy.enabled) or ("memory" in capability_set)),
        credentials=credentials_access,
        subagents=_enabled_mode(enabled=(not policy.enabled) or ("subagents" in capability_set)),
        automation=_enabled_mode(enabled=(not policy.enabled) or ("automation" in capability_set)),
        http=_enabled_mode(enabled=(not policy.enabled) or ("http" in capability_set)),
        web=_enabled_mode(enabled=(not policy.enabled) or ("web" in capability_set)),
        browser=_enabled_mode(enabled=(not policy.enabled) or ("browser" in capability_set)),
        skills=_enabled_mode(enabled=(not policy.enabled) or ("skills" in capability_set)),
        apps=_enabled_mode(enabled=(not policy.enabled) or ("apps" in capability_set)),
        debug=_enabled_mode(enabled=(not policy.enabled) or ("debug" in capability_set)),
    )
    if channel_tool_profile is None:
        return summary
    return _apply_channel_tool_profile_to_tool_access(
        summary=summary,
        channel_tool_profile=channel_tool_profile,
    )


def _apply_channel_tool_profile_to_tool_access(
    *,
    summary: ToolAccessSummary,
    channel_tool_profile: ChannelToolProfile,
) -> ToolAccessSummary:
    """Narrow one profile-level tool summary with the effective channel tool profile."""

    allowlist = allowed_tool_names_for_channel_profile(channel_tool_profile)
    if allowlist is None:
        return summary
    allowed = set(allowlist)
    files = summary.files
    if not any(name.startswith("file.") for name in allowed):
        files = "disabled"
    elif summary.files not in {"disabled", "none"}:
        write_like_file_tools = {"file.write", "file.edit", "file.delete", "file.move", "file.copy"}
        readonly_file_tools = {"file.list", "file.read", "file.search"}
        file_tools = {name for name in allowed if name.startswith("file.")}
        if file_tools and file_tools.issubset(readonly_file_tools):
            files = "read_only"
        elif file_tools and file_tools.isdisjoint(write_like_file_tools):
            files = "read_only" if summary.files == "read_write" else summary.files
    return ToolAccessSummary(
        files=files,
        shell=_tool_family_access(summary.shell, allowed=allowed, prefixes=("bash.",)),
        memory=_tool_family_access(summary.memory, allowed=allowed, prefixes=("memory.",)),
        credentials=summary.credentials,
        subagents=_tool_family_access(summary.subagents, allowed=allowed, prefixes=("subagent.",)),
        automation=_tool_family_access(summary.automation, allowed=allowed, prefixes=("automation.",)),
        http=_tool_family_access(summary.http, allowed=allowed, prefixes=("http.",)),
        web=_tool_family_access(summary.web, allowed=allowed, prefixes=("web.",)),
        browser=_tool_family_access(summary.browser, allowed=allowed, prefixes=("browser.",)),
        skills=_tool_family_access(summary.skills, allowed=allowed, prefixes=("skill.",)),
        apps=_tool_family_access(summary.apps, allowed=allowed, exact_names=("app.run", "app.list")),
        debug=_tool_family_access(summary.debug, allowed=allowed, prefixes=("debug.",)),
    )


def _tool_family_access(
    current: str,
    *,
    allowed: set[str],
    prefixes: tuple[str, ...] = (),
    exact_names: tuple[str, ...] = (),
) -> str:
    """Return one tool-family status after applying one explicit channel allowlist."""

    if current == "disabled":
        return current
    has_allowed_tool = (
        bool(prefixes) and any(name.startswith(prefixes) for name in allowed)
    ) or any(name in allowed for name in exact_names)
    return current if has_allowed_tool else "disabled"


def _enabled_mode(*, enabled: bool) -> str:
    return "enabled" if enabled else "disabled"


def _infer_network_access(*, policy: ProfilePolicyView) -> str:
    if not policy.enabled:
        return "unrestricted"
    allowlist = tuple(item for item in policy.network_allowlist if item)
    if not allowlist:
        return "deny"
    if "*" in allowlist:
        return "unrestricted"
    return "allowlist"


def _infer_file_scope_mode(
    *,
    settings: Settings,
    policy: ProfilePolicyView,
    profile_root: Path,
) -> str:
    if not policy.enabled:
        return "full_system"
    if not policy.allowed_directories:
        return "none"
    return infer_workspace_scope_mode(
        root_dir=settings.root_dir,
        profile_root=profile_root,
        allowed_directories=policy.allowed_directories,
    )


def _build_memory_behavior_summary(
    *,
    policy: ProfilePolicyView,
    runtime: ProfileRuntimeResolved,
    transport: str | None,
) -> MemoryBehaviorSummary:
    capability_set = set(_effective_capability_ids(policy=policy))
    memory_enabled = (not policy.enabled) or ("memory" in capability_set)
    if not memory_enabled:
        explicit_cross_chat_access = "disabled"
    elif transport is not None and is_user_facing_transport(transport):
        explicit_cross_chat_access = "trusted_only"
    else:
        explicit_cross_chat_access = "allowed_with_selectors"
    return MemoryBehaviorSummary(
        capability=_enabled_mode(enabled=memory_enabled),
        auto_search_enabled=runtime.memory_auto_search_enabled,
        auto_search_scope_mode=runtime.memory_auto_search_scope_mode,
        auto_search_chat_limit=runtime.memory_auto_search_chat_limit,
        auto_search_global_limit=runtime.memory_auto_search_global_limit,
        auto_search_include_global=runtime.memory_auto_search_include_global,
        auto_save_enabled=runtime.memory_auto_save_enabled,
        auto_save_scope_mode=runtime.memory_auto_save_scope_mode,
        auto_save_kinds=tuple(runtime.memory_auto_save_kinds),
        auto_promote_enabled=runtime.memory_auto_promote_enabled,
        global_fallback_enabled=runtime.memory_global_fallback_enabled,
        explicit_cross_chat_access=explicit_cross_chat_access,
    )


def _display_path(*, settings: Settings, path: Path) -> str:
    candidate = path if path.is_absolute() else settings.root_dir / path
    resolved = candidate.resolve(strict=False)
    root = settings.root_dir.resolve(strict=False)
    try:
        return str(resolved.relative_to(root))
    except ValueError:
        return str(resolved)


def _mutation_merge_order() -> tuple[str, ...]:
    """Return the canonical mutation precedence used by setup/profile/channel flows."""

    return (
        "explicit_cli_overrides",
        "persisted_current_values",
        "inherited_defaults",
        "system_defaults",
    )


def _profile_current_runtime_overrides(profile: ProfileDetails) -> dict[str, object]:
    """Return persisted runtime overrides currently stored for one profile."""

    if profile.runtime_config is None:
        return {}
    return profile.runtime_config.model_dump(mode="json", exclude_none=True)


def _profile_current_policy_overrides(profile: ProfileDetails) -> dict[str, object]:
    """Return persisted policy state currently stored for one profile."""

    return {
        "enabled": profile.policy.enabled,
        "preset": profile.policy.preset,
        "capabilities": list(profile.policy.capabilities),
        "file_access_mode": profile.policy.file_access_mode,
        "allowed_directories": list(profile.policy.allowed_directories),
        "network_allowlist": list(profile.policy.network_allowlist),
    }


def _channel_current_overrides(channel: ChannelEndpointConfig) -> dict[str, object]:
    """Return persisted non-default channel overrides beyond base identity fields."""

    payload = channel.model_dump(mode="json", exclude_defaults=True)
    for field_name in (
        "endpoint_id",
        "transport",
        "adapter_kind",
        "profile_id",
        "credential_profile_key",
        "account_id",
        "config",
    ):
        payload.pop(field_name, None)
    return payload
