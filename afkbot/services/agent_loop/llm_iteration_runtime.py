"""Iterative LLM planning runtime for tool-calling turns."""

from __future__ import annotations

import json
import time
from typing import cast
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.action_contracts import ActionEnvelope
from afkbot.services.agent_loop.llm_request_runtime import LLMRequestRuntime
from afkbot.services.agent_loop.llm_tool_followup import LLMToolFollowupPolicy
from afkbot.services.agent_loop.pending_envelopes import PendingEnvelopeBuilder
from afkbot.services.agent_loop.state_machine import StateMachine
from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
from afkbot.services.llm.contracts import (
    LLMMessage,
    LLMRequest,
    LLMToolDefinition,
    ToolCallRequest,
)
from afkbot.services.llm.reasoning import ReasoningEffort
from afkbot.services.tools.base import ToolCall, ToolResult

AsyncProgressLogger = Callable[..., Awaitable[None]]
AsyncCancelCheck = Callable[..., Awaitable[None]]
NormalizeParams = Callable[[object], dict[str, object]]
SanitizeText = Callable[[str], str]
SanitizeValue = Callable[[object], object]


@dataclass(frozen=True, slots=True)
class LLMIterationResult:
    """Result of one iterative LLM planning run."""

    assistant_message: str
    error_code: str | None = None
    pending_envelope: ActionEnvelope | None = None


class LLMIterationRuntime:
    """Run iterative LLM planning with tool execution and bounded follow-up hints."""

    def __init__(
        self,
        *,
        llm_request_runtime: LLMRequestRuntime,
        tool_execution: ToolExecutionRuntime,
        pending_envelopes: PendingEnvelopeBuilder,
        tool_skill_resolver: ToolSkillResolver,
        log_progress: AsyncProgressLogger,
        raise_if_cancel_requested: AsyncCancelCheck,
        sanitize: SanitizeText,
        sanitize_value: SanitizeValue,
        to_params_dict: NormalizeParams,
    ) -> None:
        self._llm_request_runtime = llm_request_runtime
        self._tool_execution = tool_execution
        self._pending_envelopes = pending_envelopes
        self._tool_followup_policy = LLMToolFollowupPolicy(
            tool_skill_resolver=tool_skill_resolver,
        )
        self._log_progress = log_progress
        self._raise_if_cancel_requested = raise_if_cancel_requested
        self._sanitize = sanitize
        self._sanitize_value = sanitize_value
        self._to_params_dict = to_params_dict

    async def run(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        policy: ProfilePolicy,
        context: str,
        history: list[LLMMessage],
        machine: StateMachine,
        available_tools: tuple[LLMToolDefinition, ...],
        max_iterations: int,
        request_timeout_sec: float,
        wall_clock_budget_sec: float,
        reasoning_effort: ReasoningEffort | None,
        automation_intent: bool,
        explicit_skill_requests: set[str] | None,
        explicit_subagent_requests: set[str] | None,
        emit_planning_progress: bool = True,
        runtime_metadata: dict[str, object] | None = None,
    ) -> LLMIterationResult:
        """Execute one iterative LLM loop with sequential guarded tool calls."""

        visible_tool_names = {tool.name for tool in available_tools}
        effective_allowed_tool_names = self._resolve_allowed_tool_names(
            visible_tool_names=visible_tool_names,
            runtime_metadata=runtime_metadata,
        )
        consecutive_missing_file_reads = 0
        started_at = time.monotonic()
        deadline = started_at + max(0.01, float(wall_clock_budget_sec))

        for iteration in range(1, max_iterations + 1):
            await self._raise_if_cancel_requested(run_id=run_id)
            remaining_sec = deadline - time.monotonic()
            if remaining_sec <= 0:
                return LLMIterationResult(
                    assistant_message=self._wall_clock_budget_message(
                        wall_clock_budget_sec=wall_clock_budget_sec,
                    )
                )
            await self._log_progress(
                run_id=run_id,
                session_id=session_id,
                stage="llm_iteration",
                iteration=iteration,
            )
            request = LLMRequest(
                profile_id=profile_id,
                session_id=session_id,
                context=context,
                history=history,
                available_tools=available_tools,
                reasoning_effort=reasoning_effort,
                request_timeout_sec=max(0.01, min(request_timeout_sec, remaining_sec)),
            )
            response = await self._llm_request_runtime.complete_with_progress(
                run_id=run_id,
                session_id=session_id,
                iteration=iteration,
                request=request,
            )

            if response.kind == "final":
                final_message = self._sanitize(response.final_message or "finalized: empty")
                if response.error_code:
                    return LLMIterationResult(
                        assistant_message=final_message,
                        error_code=response.error_code,
                    )
                return LLMIterationResult(assistant_message=final_message)

            normalized_calls = [
                ToolCallRequest(
                    name=str(call.name).strip(),
                    params=self._to_params_dict(call.params),
                    call_id=(call.call_id or "").strip() or f"call_{iteration}_{idx}",
                )
                for idx, call in enumerate(response.tool_calls, start=1)
            ]
            machine.execute_tools()
            await self._log_progress(
                run_id=run_id,
                session_id=session_id,
                stage="tool_executing",
                iteration=iteration,
            )
            tool_calls = [
                ToolCall(
                    name=call.name,
                    params=self._to_params_dict(call.params),
                )
                for call in normalized_calls
            ]
            results = await self._tool_execution.execute_requested_tool_calls(
                run_id=run_id,
                session_id=session_id,
                profile_id=profile_id,
                tool_calls=tool_calls,
                policy=policy,
                automation_intent=automation_intent,
                explicit_skill_requests=explicit_skill_requests,
                explicit_subagent_requests=explicit_subagent_requests,
                allow_confirmation_markers=False,
                runtime_metadata=runtime_metadata,
                allowed_tool_names=effective_allowed_tool_names,
            )
            pending_envelope = self._build_pending_envelope(
                tool_calls=tool_calls,
                tool_results=results,
            )
            if pending_envelope is not None:
                return LLMIterationResult(
                    assistant_message="",
                    pending_envelope=pending_envelope,
                )
            remaining_sec = deadline - time.monotonic()
            if remaining_sec <= 0:
                return LLMIterationResult(
                    assistant_message=self._wall_clock_budget_message(
                        wall_clock_budget_sec=wall_clock_budget_sec,
                    )
                )
            self._append_tool_call_history(
                history=history,
                assistant_provider_items=response.provider_items,
                normalized_calls=normalized_calls,
                tool_calls=tool_calls,
                tool_results=results,
            )
            followup = self._tool_followup_policy.determine(
                tool_calls=tool_calls,
                tool_results=results,
                visible_tool_names=visible_tool_names,
                consecutive_missing_file_reads=consecutive_missing_file_reads,
                profile_id=profile_id,
            )
            consecutive_missing_file_reads = followup.consecutive_missing_file_reads
            if followup.final_message is not None:
                return LLMIterationResult(assistant_message=followup.final_message)
            if followup.history_prompt is not None:
                history.append(
                    LLMMessage(
                        role="user",
                        content=followup.history_prompt,
                    )
                )

            machine.think()
            await self._log_progress(
                run_id=run_id,
                session_id=session_id,
                stage="thinking",
                iteration=iteration,
            )
            machine.plan()
            if emit_planning_progress:
                await self._log_progress(
                    run_id=run_id,
                    session_id=session_id,
                    stage="planning",
                    iteration=iteration,
                )

        return LLMIterationResult(
            assistant_message=f"finalized: max_iterations_reached ({max_iterations})",
        )

    @staticmethod
    def _wall_clock_budget_message(*, wall_clock_budget_sec: float) -> str:
        """Return deterministic final message when total loop runtime budget is exhausted."""

        return f"finalized: runtime_budget_reached ({wall_clock_budget_sec:.2f}s)"

    def _build_pending_envelope(
        self,
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> ActionEnvelope | None:
        """Convert tool failures into pending user-interaction envelopes."""

        pending_envelope = self._pending_envelopes.build_tool_not_allowed_envelope(
            tool_calls=tool_calls,
            tool_results=tool_results,
        )
        if pending_envelope is None:
            pending_envelope = self._pending_envelopes.build_profile_selection_envelope(
                tool_calls=tool_calls,
                tool_results=tool_results,
            )
        if pending_envelope is None:
            pending_envelope = self._pending_envelopes.build_secure_envelope(
                tool_calls=tool_calls,
                tool_results=tool_results,
            )
        if pending_envelope is None:
            pending_envelope = self._pending_envelopes.build_approval_envelope(
                tool_calls=tool_calls,
                tool_results=tool_results,
            )
        return pending_envelope

    @staticmethod
    def _resolve_allowed_tool_names(
        *,
        visible_tool_names: set[str],
        runtime_metadata: dict[str, object] | None,
    ) -> set[str]:
        """Return execution guard allowlist merged with session-explicit tool access."""

        if not runtime_metadata:
            return visible_tool_names
        raw_allowed = runtime_metadata.get("session_allowed_tool_names")
        if raw_allowed is None:
            return visible_tool_names

        session_allowed: set[str] = set()
        if isinstance(raw_allowed, (list, tuple, set)):
            for raw_name in raw_allowed:
                tool_name = str(raw_name).strip()
                if tool_name:
                    session_allowed.add(tool_name)
        elif isinstance(raw_allowed, str):
            for raw_name in raw_allowed.split(","):
                tool_name = raw_name.strip()
                if tool_name:
                    session_allowed.add(tool_name)

        if not session_allowed:
            return visible_tool_names
        return visible_tool_names | session_allowed

    def _append_tool_call_history(
        self,
        *,
        history: list[LLMMessage],
        assistant_provider_items: list[dict[str, object]],
        normalized_calls: list[ToolCallRequest],
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> None:
        """Append assistant tool call request and tool results to provider history."""

        history.append(
            LLMMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCallRequest(
                        name=self._sanitize(call.name),
                        params=self._to_params_dict(self._sanitize_value(call.params)),
                        call_id=call.call_id,
                    )
                    for call in normalized_calls
                ],
                provider_items=[
                    item
                    for item in cast(
                        list[dict[str, object]],
                        self._sanitize_value(assistant_provider_items),
                    )
                    if isinstance(item, dict)
                ],
            )
        )
        for tool_call_request, tool_call, result in zip(
            normalized_calls,
            tool_calls,
            tool_results,
            strict=True,
        ):
            result_payload = self._sanitize_value(result.model_dump())
            history.append(
                LLMMessage(
                    role="tool",
                    tool_name=tool_call.name,
                    tool_call_id=tool_call_request.call_id,
                    content=json.dumps(result_payload, ensure_ascii=True, sort_keys=True),
                )
            )
