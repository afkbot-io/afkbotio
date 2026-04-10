"""Turn execution runtime extracted from AgentLoop coordinator."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Literal

from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.llm_iteration_runtime import LLMIterationRuntime
from afkbot.services.agent_loop.pending_envelopes import PendingEnvelopeBuilder
from afkbot.services.agent_loop.planning_policy import ChatPlanningMode
from afkbot.services.agent_loop.runlog_runtime import RunlogRuntime
from afkbot.services.agent_loop.security_guard import SecurityGuard
from afkbot.services.agent_loop.sessions import SessionService
from afkbot.services.agent_loop.state_machine import StateMachine
from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
from afkbot.services.agent_loop.turn_execution_context import resolve_turn_execution_context
from afkbot.services.agent_loop.turn_finalizer import TurnFinalizer
from afkbot.services.agent_loop.turn_planning_artifacts import (
    planned_tools_final_message,
    turn_plan_payload,
)
from afkbot.services.agent_loop.turn_context import (
    TurnContextOverrides,
)
from afkbot.services.agent_loop.turn_preparation import TurnPreparationRuntime
from afkbot.services.llm.reasoning import ThinkingLevel
from afkbot.services.policy import PolicyEngine
from afkbot.services.tools.base import ToolCall


class TurnExecutionRuntime:
    """Execute one agent turn using already-wired runtime dependencies."""

    def __init__(
        self,
        *,
        profile_repo: ProfileRepository,
        policy_repo: ProfilePolicyRepository,
        sessions: SessionService,
        security_guard: SecurityGuard,
        turn_preparation: TurnPreparationRuntime,
        run_repo: RunRepository,
        runlog: RunlogRuntime,
        tool_execution: ToolExecutionRuntime,
        pending_envelopes: PendingEnvelopeBuilder,
        llm_provider_enabled: bool,
        llm_iterations: LLMIterationRuntime | None,
        policy_engine: PolicyEngine,
        llm_max_iterations: int,
        default_thinking_level: ThinkingLevel,
        chat_planning_mode: ChatPlanningMode,
        llm_request_timeout_sec: float,
        llm_execution_budget_low_sec: float,
        llm_execution_budget_medium_sec: float,
        llm_execution_budget_high_sec: float,
        llm_execution_budget_very_high_sec: float,
        turn_finalizer: TurnFinalizer,
        sanitize: Callable[[str], str],
        sanitize_value: Callable[[object], object],
    ) -> None:
        self._profile_repo = profile_repo
        self._policy_repo = policy_repo
        self._sessions = sessions
        self._security_guard = security_guard
        self._turn_preparation = turn_preparation
        self._run_repo = run_repo
        self._runlog = runlog
        self._tool_execution = tool_execution
        self._pending_envelopes = pending_envelopes
        self._llm_provider_enabled = llm_provider_enabled
        self._llm_iterations = llm_iterations
        self._policy_engine = policy_engine
        self._llm_max_iterations = llm_max_iterations
        self._default_thinking_level = default_thinking_level
        self._chat_planning_mode = chat_planning_mode
        self._llm_request_timeout_sec = llm_request_timeout_sec
        self._llm_execution_budget_low_sec = llm_execution_budget_low_sec
        self._llm_execution_budget_medium_sec = llm_execution_budget_medium_sec
        self._llm_execution_budget_high_sec = llm_execution_budget_high_sec
        self._llm_execution_budget_very_high_sec = llm_execution_budget_very_high_sec
        self._turn_finalizer = turn_finalizer
        self._sanitize = sanitize
        self._sanitize_value = sanitize_value

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        planned_tool_calls: list[ToolCall] | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        """Execute one turn and persist run, turn, and runlog artifacts."""

        machine = StateMachine()
        run_id: int | None = None
        session_key: str | None = None
        final_spec_patch: dict[str, object] | None = None
        raw_user_message = message.strip()
        user_guard = self._security_guard.check_user_message(text=raw_user_message)
        user_message = self._sanitize(user_guard.redacted_text)
        normalized_planned_tool_calls = planned_tool_calls if planned_tool_calls else None

        try:
            await self._profile_repo.get_or_create_default(profile_id)
            policy = await self._policy_repo.get_or_create_default(profile_id)
            session_key = await self._sessions.get_or_create(
                session_id=session_id,
                profile_id=profile_id,
            )

            run = await self._run_repo.create_run(
                session_id=session_key,
                profile_id=profile_id,
                status="running",
            )
            run_id = run.id
            await self._runlog.raise_if_cancel_requested(run_id=run_id)
            resolved_context = resolve_turn_execution_context(
                policy=policy,
                policy_engine=self._policy_engine,
                runtime_limit=self._llm_max_iterations,
                base_timeout_sec=self._llm_request_timeout_sec,
                default_thinking_level=self._default_thinking_level,
                chat_planning_mode=self._chat_planning_mode,
                execution_budget_low_sec=self._llm_execution_budget_low_sec,
                execution_budget_medium_sec=self._llm_execution_budget_medium_sec,
                execution_budget_high_sec=self._llm_execution_budget_high_sec,
                execution_budget_very_high_sec=self._llm_execution_budget_very_high_sec,
                raw_user_message=raw_user_message,
                context_overrides=context_overrides,
            )
            thinking_config = resolved_context.thinking_config
            execution_planning_mode = resolved_context.execution_planning_mode
            execution_planning_enabled = resolved_context.execution_planning_enabled
            effective_overrides = resolved_context.effective_overrides

            if not user_guard.allow:
                blocked_message = (
                    "Secret-like input is blocked in chat flow. "
                    "Use request_secure_field and credentials tools instead."
                )
                return await self._turn_finalizer.finalize_blocked_user_input(
                    run_id=run_id,
                    session_id=session_key,
                    profile_id=profile_id,
                    user_message=user_message,
                    blocked_message=blocked_message,
                    blocked_reason=user_guard.error_code,
                    machine_state=machine.state.value,
                )

            prepared = await self._turn_preparation.prepare(
                run_id=run_id,
                session_id=session_key,
                profile_id=profile_id,
                policy=policy,
                raw_user_message=raw_user_message,
                user_message=user_message,
                llm_enabled=self._llm_provider_enabled,
                context_overrides=effective_overrides,
            )
            automation_intent = prepared.automation_intent
            explicit_skill_mentions = prepared.explicit_skill_mentions
            explicit_enforceable_skill_mentions = prepared.explicit_enforceable_skill_mentions
            explicit_subagent_mentions = prepared.explicit_subagent_mentions
            skill_route = prepared.skill_route
            unavailable_explicit_skill_message = prepared.unavailable_explicit_skill_message
            context_sanitized = self._sanitize(str(self._sanitize_value(prepared.context)))
            available_tools = prepared.available_tools
            executable_tool_names = set(prepared.executable_tool_names)
            approval_required_tool_names = set(prepared.approval_required_tool_names)
            history = prepared.history

            machine.think()
            await self._runlog.log_event(
                run_id=run_id,
                session_id=session_key,
                event_type="turn.think",
                payload={"state": machine.state.value, "message": user_message},
            )
            await self._runlog.log_progress(
                run_id=run_id,
                session_id=session_key,
                stage="thinking",
                iteration=0,
            )

            public_planning_enabled = effective_overrides.planning_mode == "plan_only"
            should_emit_plan_progress = (
                public_planning_enabled
                or normalized_planned_tool_calls is not None
                or not self._llm_provider_enabled
            )

            machine.plan()
            await self._runlog.log_event(
                run_id=run_id,
                session_id=session_key,
                event_type="turn.plan",
                payload=turn_plan_payload(
                    machine_state=machine.state.value,
                    skill_route=skill_route,
                    explicit_skill_mentions=explicit_skill_mentions,
                    explicit_enforceable_skill_mentions=explicit_enforceable_skill_mentions,
                    explicit_subagent_mentions=explicit_subagent_mentions,
                    available_tools=available_tools,
                    planned_tool_calls=normalized_planned_tool_calls,
                    planning_mode="off" if effective_overrides is None else effective_overrides.planning_mode,
                    chat_planning_mode=execution_planning_mode,
                    execution_planning_enabled=execution_planning_enabled,
                    thinking_level=thinking_config.thinking_level,
                    tool_access_mode=thinking_config.tool_access_mode,
                    request_timeout_sec=thinking_config.request_timeout_sec,
                    wall_clock_budget_sec=thinking_config.wall_clock_budget_sec,
                ),
            )
            if should_emit_plan_progress:
                await self._runlog.log_progress(
                    run_id=run_id,
                    session_id=session_key,
                    stage="planning",
                    iteration=0,
                )

            pending_envelope: ActionEnvelope | None = None
            visible_tool_names_for_planned_execution = (
                executable_tool_names
                if self._llm_provider_enabled
                else {
                    tool_name.strip()
                    for tool_name in (call.name for call in normalized_planned_tool_calls or ())
                    if tool_name.strip()
                }
            )
            effective_allowed_tool_names = (
                set(visible_tool_names_for_planned_execution)
                if normalized_planned_tool_calls
                else set(executable_tool_names)
            )

            if unavailable_explicit_skill_message is not None and not normalized_planned_tool_calls:
                assistant_message = unavailable_explicit_skill_message
            elif normalized_planned_tool_calls:
                machine.execute_tools()
                await self._runlog.log_progress(
                    run_id=run_id,
                    session_id=session_key,
                    stage="tool_executing",
                    iteration=0,
                )
                results = await self._tool_execution.execute_requested_tool_calls(
                    run_id=run_id,
                    session_id=session_key,
                    profile_id=profile_id,
                    tool_calls=normalized_planned_tool_calls,
                    policy=policy,
                    automation_intent=automation_intent,
                    explicit_skill_requests=explicit_skill_mentions,
                    explicit_subagent_requests=explicit_subagent_mentions,
                    allow_confirmation_markers=True,
                    runtime_metadata=(
                        None if effective_overrides is None else effective_overrides.runtime_metadata
                    ),
                    allowed_tool_names=effective_allowed_tool_names,
                    approved_tool_names=(
                        None
                        if effective_overrides is None
                        or effective_overrides.approved_tool_names is None
                        else set(effective_overrides.approved_tool_names)
                    ),
                    approval_required_tool_names=approval_required_tool_names,
                )
                pending_envelope = self._pending_envelopes.build_tool_not_allowed_envelope(
                    tool_calls=normalized_planned_tool_calls,
                    tool_results=results,
                )
                if pending_envelope is None:
                    pending_envelope = self._pending_envelopes.build_profile_selection_envelope(
                        tool_calls=normalized_planned_tool_calls,
                        tool_results=results,
                    )
                if pending_envelope is None:
                    pending_envelope = self._pending_envelopes.build_secure_envelope(
                        tool_calls=normalized_planned_tool_calls,
                        tool_results=results,
                    )
                if pending_envelope is None:
                    pending_envelope = self._pending_envelopes.build_approval_envelope(
                        tool_calls=normalized_planned_tool_calls,
                        tool_results=results,
                    )
                if pending_envelope is not None:
                    return await self._turn_finalizer.finalize_pending_envelope(
                        run_id=run_id,
                        session_id=session_key,
                        profile_id=profile_id,
                        user_message=user_message,
                        machine_state=machine.state.value,
                        envelope=pending_envelope,
                    )
                machine.think()
                machine.plan()
                assistant_message = planned_tools_final_message(
                    user_message=user_message,
                    tool_calls=normalized_planned_tool_calls,
                    tool_results=results,
                )
            elif self._llm_provider_enabled:
                assert self._llm_iterations is not None
                llm_result = await self._llm_iterations.run(
                    run_id=run_id,
                    session_id=session_key,
                    profile_id=profile_id,
                    policy=policy,
                    context=context_sanitized,
                    history=history,
                    machine=machine,
                    available_tools=available_tools,
                    executable_tool_names=tuple(executable_tool_names),
                    max_iterations=thinking_config.max_iterations,
                    request_timeout_sec=thinking_config.request_timeout_sec,
                    wall_clock_budget_sec=thinking_config.wall_clock_budget_sec,
                    reasoning_effort=thinking_config.reasoning_effort,
                    automation_intent=automation_intent,
                    explicit_skill_requests=explicit_skill_mentions,
                    explicit_subagent_requests=explicit_subagent_mentions,
                    emit_planning_progress=public_planning_enabled,
                    runtime_metadata=(
                        None if effective_overrides is None else effective_overrides.runtime_metadata
                    ),
                    approved_tool_names=(
                        None if effective_overrides is None else effective_overrides.approved_tool_names
                    ),
                    approval_required_tool_names=tuple(approval_required_tool_names),
                )
                assistant_message = llm_result.assistant_message
                pending_envelope = llm_result.pending_envelope
                if llm_result.error_code:
                    final_spec_patch = {"error_code": llm_result.error_code}
                if pending_envelope is not None:
                    return await self._turn_finalizer.finalize_pending_envelope(
                        run_id=run_id,
                        session_id=session_key,
                        profile_id=profile_id,
                        user_message=user_message,
                        machine_state=machine.state.value,
                        envelope=pending_envelope,
                    )
            else:
                assistant_message = (
                    "LLM provider is not configured. "
                    "I could not execute this request."
                )
                final_spec_patch = {"error_code": "llm_provider_not_configured"}

            blocked_reason, action, assistant_message = await self._resolve_assistant_message(
                assistant_message=assistant_message,
                machine=machine,
                run_id=run_id,
                session_id=session_key,
                user_message=user_message,
            )

            await self._runlog.raise_if_cancel_requested(run_id=run_id)
            machine.finalize()
            return await self._turn_finalizer.finalize_turn(
                run_id=run_id,
                session_id=session_key,
                profile_id=profile_id,
                user_message=user_message,
                assistant_message=assistant_message,
                action=action,
                blocked_reason=blocked_reason,
                machine_state=machine.state.value,
                policy=policy,
                runtime_metadata=(
                    None if effective_overrides is None else effective_overrides.runtime_metadata
                ),
                spec_patch=final_spec_patch,
            )
        except asyncio.CancelledError:
            if run_id is not None and session_key is not None:
                await self._turn_finalizer.finalize_cancelled_turn(
                    run_id=run_id,
                    session_id=session_key,
                    machine=machine,
                )
            raise

    async def _resolve_assistant_message(
        self,
        *,
        assistant_message: str,
        machine: StateMachine,
        run_id: int,
        session_id: str,
        user_message: str,
    ) -> tuple[str | None, Literal["block", "finalize"], str]:
        assistant_guard = self._security_guard.check_assistant_message(text=assistant_message)
        blocked_reason = assistant_guard.error_code
        if assistant_guard.allow:
            return blocked_reason, "finalize", assistant_guard.redacted_text

        sanitized_message = (
            "Secret-like output was blocked. "
            "Use request_secure_field and credentials tools instead."
        )
        await self._runlog.log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="turn.block",
            payload={
                "user_message": user_message,
                "assistant_message": sanitized_message,
                "blocked_reason": blocked_reason,
                "state": machine.state.value,
            },
        )
        return blocked_reason, "block", sanitized_message
