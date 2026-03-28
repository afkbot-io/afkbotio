"""Profile-aware AgentLoop runtime construction helpers."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.llm.contracts import LLMProvider
from afkbot.services.llm.provider import build_llm_provider
from afkbot.services.policy import PolicyEngine
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.registry import ToolRegistry
from afkbot.services.tools.workspace import resolve_tool_workspace_base_dir
from afkbot.settings import Settings


def resolve_profile_settings(
    *,
    settings: Settings,
    profile_id: str,
    ensure_layout: bool = False,
) -> Settings:
    """Resolve effective settings for one profile by applying stored overrides."""

    return get_profile_runtime_config_service(settings).build_effective_settings(
        profile_id=profile_id,
        base_settings=settings,
        ensure_layout=ensure_layout,
    )


def build_profile_agent_loop(
    session: AsyncSession,
    *,
    settings: Settings,
    profile_id: str,
    actor: Literal["main", "subagent"] = "main",
    llm_provider_override: LLMProvider | None = None,
) -> AgentLoop:
    """Build AgentLoop with profile-specific runtime overrides applied."""

    effective_settings = resolve_profile_settings(
        settings=settings,
        profile_id=profile_id,
        ensure_layout=True,
    )
    workspace_root = resolve_tool_workspace_base_dir(
        settings=effective_settings,
        profile_id=profile_id,
    )
    return build_agent_loop_from_settings(
        session,
        settings=effective_settings,
        actor=actor,
        llm_provider_override=llm_provider_override,
        policy_engine=PolicyEngine(root_dir=workspace_root),
        profile_id=profile_id,
    )


def build_agent_loop_from_settings(
    session: AsyncSession,
    *,
    settings: Settings,
    actor: Literal["main", "subagent"] = "main",
    llm_provider_override: LLMProvider | None = None,
    policy_engine: PolicyEngine | None = None,
    profile_id: str | None = None,
) -> AgentLoop:
    """Build AgentLoop from already-resolved settings."""

    effective_settings = settings
    return AgentLoop(
        session,
        ContextBuilder(effective_settings, SkillLoader(effective_settings)),
        tool_registry=(
            ToolRegistry.from_settings(effective_settings)
            if profile_id is None
            else ToolRegistry.from_profile_settings(
                effective_settings,
                profile_id=profile_id,
            )
        ),
        llm_provider=build_llm_provider(effective_settings)
        if llm_provider_override is None
        else llm_provider_override,
        llm_request_timeout_sec=effective_settings.llm_request_timeout_sec,
        llm_max_iterations=effective_settings.llm_max_iterations,
        llm_default_thinking_level=effective_settings.llm_thinking_level,
        chat_planning_mode=effective_settings.chat_planning_mode,
        llm_execution_budget_low_sec=effective_settings.llm_execution_budget_low_sec,
        llm_execution_budget_medium_sec=effective_settings.llm_execution_budget_medium_sec,
        llm_execution_budget_high_sec=effective_settings.llm_execution_budget_high_sec,
        llm_execution_budget_very_high_sec=effective_settings.llm_execution_budget_very_high_sec,
        llm_history_turns=effective_settings.llm_history_turns,
        tool_timeout_default_sec=effective_settings.tool_timeout_default_sec,
        tool_timeout_max_sec=effective_settings.tool_timeout_max_sec,
        secure_request_ttl_sec=effective_settings.secure_request_ttl_sec,
        policy_engine=policy_engine,
        memory_auto_search_enabled=effective_settings.memory_auto_search_enabled,
        memory_auto_search_scope_mode=effective_settings.memory_auto_search_scope_mode,
        memory_auto_search_limit=effective_settings.memory_auto_search_limit,
        memory_auto_search_include_global=effective_settings.memory_auto_search_include_global,
        memory_auto_search_chat_limit=effective_settings.memory_auto_search_chat_limit,
        memory_auto_search_global_limit=effective_settings.memory_auto_search_global_limit,
        memory_global_fallback_enabled=effective_settings.memory_global_fallback_enabled,
        memory_auto_context_item_chars=effective_settings.memory_auto_context_item_chars,
        memory_auto_save_enabled=effective_settings.memory_auto_save_enabled,
        memory_auto_save_scope_mode=effective_settings.memory_auto_save_scope_mode,
        memory_auto_promote_enabled=effective_settings.memory_auto_promote_enabled,
        memory_auto_save_kinds=tuple(effective_settings.memory_auto_save_kinds),
        memory_auto_save_max_chars=effective_settings.memory_auto_save_max_chars,
        session_compaction_enabled=effective_settings.session_compaction_enabled,
        session_compaction_trigger_turns=effective_settings.session_compaction_trigger_turns,
        session_compaction_keep_recent_turns=effective_settings.session_compaction_keep_recent_turns,
        session_compaction_max_chars=effective_settings.session_compaction_max_chars,
        session_compaction_prune_raw_turns=effective_settings.session_compaction_prune_raw_turns,
        actor=actor,
    )
