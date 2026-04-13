"""Prepare skill-routed context, tool surface, and history for one turn."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.browser_carryover import BrowserCarryoverService
from afkbot.services.agent_loop.chat_history_builder import ChatHistoryBuilder
from afkbot.services.agent_loop.context_builder import ContextAssets, ContextBuilder
from afkbot.services.agent_loop.explicit_requests import (
    explicit_skill_references,
    visible_executable_explicit_skills,
)
from afkbot.services.agent_loop.memory_runtime import MemoryRuntime
from afkbot.services.agent_loop.parallel_planning import build_parallel_strategy_note
from afkbot.services.agent_loop.runtime_facts import TrustedRuntimeFactsService
from afkbot.services.agent_loop.safety_policy import SafetyPolicy
from afkbot.services.agent_loop.session_skill_affinity import SessionSkillAffinityService
from afkbot.services.agent_loop.skill_router import SkillRoute, SkillRouter
from afkbot.services.agent_loop.thinking import combine_prompt_overlays
from afkbot.services.agent_loop.tool_exposure import ToolExposureBuilder
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.agent_loop.turn_preparation_support import (
    combine_trusted_runtime_notes as _combine_trusted_runtime_notes,
)
from afkbot.services.agent_loop.turn_preparation_support import (
    enrich_runtime_metadata,
    explicit_name_mentions,
    explicit_skill_invocations,
    explicit_skill_runtime_guidance as _explicit_skill_runtime_guidance,
    explicit_skill_unavailable_message as _explicit_skill_unavailable_message,
    explicit_subagent_invocations,
    has_automation_intent,
    planned_tools_final_message as _planned_tools_final_message,
    turn_plan_payload as _turn_plan_payload,
)
from afkbot.services.llm.contracts import LLMMessage, LLMToolDefinition

planned_tools_final_message = _planned_tools_final_message
turn_plan_payload = _turn_plan_payload
_PLAN_ONLY_EXECUTION_SURFACE_NOTE_MAX_TOOLS = 16


@dataclass(frozen=True, slots=True)
class PreparedTurn:
    """All turn-level inputs derived before planning or execution begins."""

    automation_intent: bool
    explicit_skill_mentions: set[str]
    explicit_enforceable_skill_mentions: set[str]
    explicit_subagent_mentions: set[str]
    skill_route: SkillRoute
    unavailable_explicit_skill_message: str | None
    context: str
    history: list[LLMMessage]
    available_tools: tuple[LLMToolDefinition, ...]
    executable_tool_names: tuple[str, ...]
    approval_required_tool_names: tuple[str, ...]


class TurnPreparationRuntime:
    """Assemble runtime metadata, context, and filtered tools for one turn."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        chat_history: ChatHistoryBuilder,
        memory_runtime: MemoryRuntime,
        safety_policy: SafetyPolicy,
        skill_affinity: SessionSkillAffinityService,
        skill_router: SkillRouter,
        tool_exposure: ToolExposureBuilder,
        browser_carryover: BrowserCarryoverService | None = None,
        runtime_facts: TrustedRuntimeFactsService | None = None,
    ) -> None:
        self._context_builder = context_builder
        self._chat_history = chat_history
        self._memory_runtime = memory_runtime
        self._safety_policy = safety_policy
        self._skill_affinity = skill_affinity
        self._skill_router = skill_router
        self._tool_exposure = tool_exposure
        self._browser_carryover = browser_carryover
        self._runtime_facts = runtime_facts

    async def prepare(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        policy: ProfilePolicy,
        raw_user_message: str,
        user_message: str,
        llm_enabled: bool,
        context_overrides: TurnContextOverrides | None = None,
    ) -> PreparedTurn:
        """Prepare context, history, and tool exposure for the current turn."""

        automation_intent = has_automation_intent(raw_user_message)
        context_assets = await self._context_builder.collect_assets(profile_id=profile_id)
        available_skill_triggers = dict(context_assets.skill_triggers)
        available_subagent_names = set(context_assets.subagent_names)
        explicit_skill_mentions = explicit_skill_references(
            message=raw_user_message,
            trigger_map=available_skill_triggers,
        )
        explicit_skill_mentions.update(
            explicit_skill_invocations(
                message=raw_user_message,
                trigger_map=context_assets.explicit_skill_triggers,
            )
        )
        affinity_skill_mentions = self._skill_affinity.resolve(
            profile_id=profile_id,
            session_id=session_id,
            raw_user_message=raw_user_message,
            explicit_skill_names=explicit_skill_mentions,
            selected_skill_names=set(),
        )
        skill_route = self._skill_router.route(
            message=raw_user_message,
            skills=context_assets.skills,
            explicit_skill_names=explicit_skill_mentions,
            affinity_skill_names=affinity_skill_mentions,
        )
        automation_intent = automation_intent or "automation" in skill_route.selected_skill_names
        self._skill_affinity.remember(
            profile_id=profile_id,
            session_id=session_id,
            selected_skill_names=skill_route.selected_skill_names,
        )
        explicit_subagent_mentions = explicit_name_mentions(
            message=raw_user_message,
            candidate_names=available_subagent_names,
        )
        explicit_subagent_mentions.update(
            explicit_subagent_invocations(
                message=raw_user_message,
                candidate_names=available_subagent_names,
            )
        )
        base_runtime_metadata = (
            None
            if context_overrides is None or not context_overrides.runtime_metadata
            else context_overrides.runtime_metadata
        )
        runtime_metadata = await self._memory_runtime.auto_search_metadata(
            run_id=run_id,
            session_id=session_id,
            profile_id=profile_id,
            user_message=user_message,
            policy=policy,
            runtime_metadata=base_runtime_metadata,
        )
        if base_runtime_metadata:
            runtime_metadata = dict(runtime_metadata or {})
            runtime_metadata.update(base_runtime_metadata)
        runtime_metadata = self._safety_policy.enrich_runtime_metadata(
            runtime_metadata=runtime_metadata,
            policy=policy,
        )
        trusted_runtime_notes = _combine_trusted_runtime_notes(
            None
            if self._runtime_facts is None
            else await self._runtime_facts.build_prompt_block(profile_id=profile_id),
            None
            if self._browser_carryover is None
            else await self._browser_carryover.build_prompt_block(
                profile_id=profile_id,
                session_id=session_id,
            ),
        )
        available_tools: tuple[LLMToolDefinition, ...] = ()
        executable_tool_names: tuple[str, ...] = ()
        approval_required_tool_names: tuple[str, ...] = ()
        prompt_overlay = None if context_overrides is None else context_overrides.prompt_overlay
        if llm_enabled:
            tool_access_mode = (
                "default"
                if context_overrides is None or context_overrides.tool_access_mode is None
                else context_overrides.tool_access_mode
            )
            tool_surface = self._tool_exposure.build_tool_surface(
                policy,
                profile_id=profile_id,
                skill_route=skill_route,
                automation_intent=automation_intent,
                runtime_metadata=runtime_metadata,
                tool_access_mode=tool_access_mode,
                approved_tool_names=(
                    None if context_overrides is None else context_overrides.approved_tool_names
                ),
                cli_approval_surface_enabled=(
                    False
                    if context_overrides is None
                    else context_overrides.cli_approval_surface_enabled
                ),
            )
            available_tools = tool_surface.visible_tools
            executable_tool_names = tool_surface.executable_tool_names
            approval_required_tool_names = tool_surface.approval_required_tool_names
            prompt_overlay = combine_prompt_overlays(
                prompt_overlay,
                self._parallel_tool_strategy_note(
                    policy=policy,
                    profile_id=profile_id,
                    skill_route=skill_route,
                    automation_intent=automation_intent,
                    runtime_metadata=runtime_metadata,
                    approved_tool_names=(
                        None if context_overrides is None else context_overrides.approved_tool_names
                    ),
                    cli_approval_surface_enabled=(
                        False
                        if context_overrides is None
                        else context_overrides.cli_approval_surface_enabled
                    ),
                    planning_mode=(
                        "off" if context_overrides is None else context_overrides.planning_mode
                    ),
                    current_visible_tool_names=tuple(tool.name for tool in available_tools),
                ),
                self._plan_only_execution_surface_note(
                    policy=policy,
                    profile_id=profile_id,
                    skill_route=skill_route,
                    automation_intent=automation_intent,
                    runtime_metadata=runtime_metadata,
                    approved_tool_names=(
                        None if context_overrides is None else context_overrides.approved_tool_names
                    ),
                    cli_approval_surface_enabled=(
                        False
                        if context_overrides is None
                        else context_overrides.cli_approval_surface_enabled
                    ),
                    planning_mode=(
                        "off" if context_overrides is None else context_overrides.planning_mode
                    ),
                    current_visible_tool_names=tuple(tool.name for tool in available_tools),
                ),
            )
        visible_enforceable_skill_names = self._tool_exposure.visible_enforceable_skill_names(
            available_tools=available_tools,
            profile_id=profile_id,
        )
        explicit_enforceable_skill_mentions = visible_executable_explicit_skills(
            context_assets=context_assets,
            explicit_skill_mentions=explicit_skill_mentions,
            available_tools=available_tools,
            visible_enforceable_skill_names=visible_enforceable_skill_names,
        )
        runtime_metadata = enrich_runtime_metadata(
            runtime_metadata=runtime_metadata,
            skill_route=skill_route,
            explicit_skill_mentions=explicit_skill_mentions,
            explicit_enforceable_skill_mentions=explicit_enforceable_skill_mentions,
            explicit_subagent_mentions=explicit_subagent_mentions,
        )
        context = await self._build_context(
            profile_id=profile_id,
            policy=policy,
            runtime_metadata=runtime_metadata,
            prompt_overlay=prompt_overlay,
            trusted_runtime_notes=trusted_runtime_notes,
            skill_route=skill_route,
            explicit_skill_mentions=explicit_skill_mentions,
            explicit_subagent_mentions=explicit_subagent_mentions,
            context_assets=context_assets,
        )
        history = await self._chat_history.build(
            profile_id=profile_id,
            session_id=session_id,
            user_message=user_message,
        )
        return PreparedTurn(
            automation_intent=automation_intent,
            explicit_skill_mentions=explicit_skill_mentions,
            explicit_enforceable_skill_mentions=explicit_enforceable_skill_mentions,
            explicit_subagent_mentions=explicit_subagent_mentions,
            skill_route=skill_route,
            unavailable_explicit_skill_message=_explicit_skill_unavailable_message(
                context_assets=context_assets,
                skill_route=skill_route,
                explicit_skill_mentions=explicit_skill_mentions,
                profile_id=profile_id,
                user_message=raw_user_message,
            ),
            context=context,
            history=history,
            available_tools=available_tools,
            executable_tool_names=executable_tool_names,
            approval_required_tool_names=approval_required_tool_names,
        )

    def _plan_only_execution_surface_note(
        self,
        *,
        policy: ProfilePolicy,
        profile_id: str,
        skill_route: SkillRoute,
        automation_intent: bool,
        runtime_metadata: dict[str, object] | None,
        approved_tool_names: tuple[str, ...] | None,
        cli_approval_surface_enabled: bool,
        planning_mode: str,
        current_visible_tool_names: tuple[str, ...],
    ) -> str | None:
        """Explain which execution tools remain available after a read-only planning pass."""

        if planning_mode != "plan_only":
            return None
        execution_surface = self._tool_exposure.build_tool_surface(
            policy,
            profile_id=profile_id,
            skill_route=skill_route,
            automation_intent=automation_intent,
            runtime_metadata=runtime_metadata,
            tool_access_mode="default",
            approved_tool_names=approved_tool_names,
            cli_approval_surface_enabled=cli_approval_surface_enabled,
        )
        current_names = set(current_visible_tool_names)
        later_visible_tools = [
            tool
            for tool in execution_surface.visible_tools
            if tool.name not in current_names
        ]
        if not later_visible_tools:
            return None
        listed_names = ", ".join(
            (
                f"`{tool.name}` (approval)"
                if tool.requires_confirmation
                else f"`{tool.name}`"
            )
            for tool in later_visible_tools[:_PLAN_ONLY_EXECUTION_SURFACE_NOTE_MAX_TOOLS]
        )
        remaining_count = len(later_visible_tools) - min(
            len(later_visible_tools),
            _PLAN_ONLY_EXECUTION_SURFACE_NOTE_MAX_TOOLS,
        )
        remainder = f", and {remaining_count} more" if remaining_count > 0 else ""
        return (
            "# Plan-Only Execution Surface\n"
            "This is a read-only planning pass. Do not call execution tools now.\n"
            "Do not claim that a tool is unavailable only because it is hidden from direct execution "
            "during planning.\n"
            "If a later execution step needs one of these tools, reference it in the plan instead of "
            "trying to call it now.\n"
            "Execution-capable tools visible after planning (approval-gated tools are marked): "
            f"{listed_names}{remainder}."
        )

    def _parallel_tool_strategy_note(
        self,
        *,
        policy: ProfilePolicy,
        profile_id: str,
        skill_route: SkillRoute,
        automation_intent: bool,
        runtime_metadata: dict[str, object] | None,
        approved_tool_names: tuple[str, ...] | None,
        cli_approval_surface_enabled: bool,
        planning_mode: str,
        current_visible_tool_names: tuple[str, ...],
    ) -> str | None:
        """Explain how to batch independent work and avoid redundant tool probing."""

        current_names = {name for name in current_visible_tool_names if name}
        future_names: set[str] = set()
        if planning_mode == "plan_only":
            execution_surface = self._tool_exposure.build_tool_surface(
                policy,
                profile_id=profile_id,
                skill_route=skill_route,
                automation_intent=automation_intent,
                runtime_metadata=runtime_metadata,
                tool_access_mode="default",
                approved_tool_names=approved_tool_names,
                cli_approval_surface_enabled=cli_approval_surface_enabled,
            )
            future_names = {
                tool.name
                for tool in execution_surface.visible_tools
                if tool.name not in current_names
            }
        known_names = current_names | future_names
        return build_parallel_strategy_note(
            known_tool_names=known_names,
            planning_mode=planning_mode,
        )

    async def _build_context(
        self,
        *,
        profile_id: str,
        policy: ProfilePolicy,
        runtime_metadata: dict[str, object] | None,
        prompt_overlay: str | None,
        trusted_runtime_notes: str | None,
        skill_route: SkillRoute,
        explicit_skill_mentions: set[str],
        explicit_subagent_mentions: set[str],
        context_assets: ContextAssets,
    ) -> str:
        """Build context and append the current runtime safety block."""

        selected_skill_names = set(skill_route.selected_skill_names)
        context = await self._context_builder.build(
            profile_id=profile_id,
            runtime_metadata=runtime_metadata,
            prompt_overlay=prompt_overlay,
            trusted_runtime_notes=trusted_runtime_notes,
            relevant_skill_names=selected_skill_names or None,
            explicit_skill_names=selected_skill_names or explicit_skill_mentions,
            explicit_subagent_names=explicit_subagent_mentions,
            assets=context_assets,
        )
        safety_block = self._safety_policy.policy_prompt_block(policy=policy)
        guidance_block = _explicit_skill_runtime_guidance(
            context_assets=context_assets,
            skill_route=skill_route,
            explicit_skill_mentions=explicit_skill_mentions,
            profile_id=profile_id,
        )
        parts = [context]
        if guidance_block:
            parts.append(f"# Explicit Skill Runtime\n{guidance_block}")
        if safety_block:
            parts.append(f"# Runtime Safety Policy\n{safety_block}")
        return "\n\n".join(parts)
