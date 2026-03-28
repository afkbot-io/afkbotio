"""Shared inspection summary models for profile and channel CLI views."""

from __future__ import annotations

from pydantic import BaseModel

from afkbot.services.channels.tool_profiles import ChannelToolProfile


class ToolAccessSummary(BaseModel):
    """Operator-facing summary of one effective tool family state."""

    files: str
    shell: str
    memory: str
    credentials: str
    subagents: str
    automation: str
    http: str
    web: str
    browser: str
    skills: str
    apps: str
    debug: str


class EffectivePermissionSummary(BaseModel):
    """High-level effective permission summary for one profile or channel."""

    policy_enabled: bool
    policy_preset: str
    capability_ids: tuple[str, ...]
    default_workspace_root: str
    shell_default_cwd: str
    file_scope_mode: str
    file_access_mode: str
    network_access: str
    network_allowlist: tuple[str, ...]
    memory_behavior: "MemoryBehaviorSummary"
    tool_access: ToolAccessSummary


class MemoryBehaviorSummary(BaseModel):
    """Operator-facing summary of effective scoped-memory behavior."""

    capability: str
    auto_search_enabled: bool
    auto_search_scope_mode: str
    auto_search_chat_limit: int
    auto_search_global_limit: int
    auto_search_include_global: bool
    auto_save_enabled: bool
    auto_save_scope_mode: str
    auto_save_kinds: tuple[str, ...]
    auto_promote_enabled: bool
    global_fallback_enabled: bool
    explicit_cross_chat_access: str


class LinkedChannelSummary(BaseModel):
    """Short profile->channel attachment summary for inspection views."""

    endpoint_id: str
    transport: str
    adapter_kind: str
    account_id: str
    enabled: bool
    mode: str


class ChannelGuardrailSummary(BaseModel):
    """Operator-facing summary of one channel-level narrowing and hard blocks."""

    user_facing_transport: bool
    channel_tool_profile: ChannelToolProfile
    channel_tool_profile_allowlist: tuple[str, ...]
    hard_blocked_tools: tuple[str, ...]


class LinkedChannelInspectionSummary(BaseModel):
    """Profile-linked channel summary with effective narrowing applied."""

    channel: LinkedChannelSummary
    mutation_state: "MutationStateSummary"
    channel_guardrails: ChannelGuardrailSummary
    profile_ceiling: EffectivePermissionSummary
    effective_permissions: EffectivePermissionSummary


class ChannelInspectionSummary(BaseModel):
    """Extended permission summary for one user-facing channel transport."""

    profile_id: str
    transport: str
    user_facing_transport: bool
    channel_tool_profile: ChannelToolProfile
    channel_tool_profile_allowlist: tuple[str, ...]
    hard_blocked_tools: tuple[str, ...]
    mutation_state: "MutationStateSummary"
    profile_ceiling: EffectivePermissionSummary
    effective_permissions: EffectivePermissionSummary


class MutationStateSummary(BaseModel):
    """Operator-facing summary of how one mutation target gets its effective state."""

    merge_order: tuple[str, ...]
    inherited_defaults_source: str
    update_preserves_unspecified: bool
    current_override_fields: tuple[str, ...]
    current_overrides: dict[str, object]
    narrowing_behavior: str | None = None


__all__ = [
    "ChannelGuardrailSummary",
    "ChannelInspectionSummary",
    "EffectivePermissionSummary",
    "LinkedChannelInspectionSummary",
    "LinkedChannelSummary",
    "MemoryBehaviorSummary",
    "MutationStateSummary",
    "ToolAccessSummary",
]
