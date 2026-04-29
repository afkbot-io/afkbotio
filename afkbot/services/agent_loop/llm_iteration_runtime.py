"""Iterative LLM planning runtime for tool-calling turns."""

from __future__ import annotations

import json
import time
from typing import cast
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.action_contracts import ActionEnvelope
from afkbot.services.agent_loop.llm_request_compaction import LLMRequestCompactionService
from afkbot.services.agent_loop.llm_request_runtime import LLMRequestRuntime
from afkbot.services.agent_loop.llm_tool_followup import LLMToolFollowupPolicy
from afkbot.services.agent_loop.pending_envelopes import PendingEnvelopeBuilder
from afkbot.services.agent_loop.state_machine import StateMachine
from afkbot.services.agent_loop.tool_execution_runtime import ToolExecutionRuntime
from afkbot.services.agent_loop.tool_skill_resolver import ToolSkillResolver
from afkbot.services.llm.contracts import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMToolDefinition,
    ToolCallRequest,
)
from afkbot.services.llm.reasoning import ReasoningEffort
from afkbot.services.tools.base import ToolCall, ToolResult

AsyncProgressLogger = Callable[..., Awaitable[None]]
AsyncEventLogger = Callable[..., Awaitable[None]]
AsyncCancelCheck = Callable[..., Awaitable[None]]
NormalizeParams = Callable[[object], dict[str, object]]
SanitizeText = Callable[[str], str]
SanitizeValue = Callable[[object], object]

_BASH_HISTORY_TAIL_LINES = 10
_BASH_HISTORY_MAX_LINE_CHARS = 240
_TOOL_HISTORY_MAX_STRING_CHARS = 800
_TOOL_HISTORY_MAX_LIST_ITEMS = 24
_TOOL_HISTORY_MAX_DICT_ITEMS = 40


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
        request_compaction: LLMRequestCompactionService,
        tool_skill_resolver: ToolSkillResolver,
        log_event: AsyncEventLogger,
        log_progress: AsyncProgressLogger,
        raise_if_cancel_requested: AsyncCancelCheck,
        sanitize: SanitizeText,
        sanitize_value: SanitizeValue,
        to_params_dict: NormalizeParams,
    ) -> None:
        self._llm_request_runtime = llm_request_runtime
        self._tool_execution = tool_execution
        self._pending_envelopes = pending_envelopes
        self._request_compaction = request_compaction
        self._tool_followup_policy = LLMToolFollowupPolicy(
            tool_skill_resolver=tool_skill_resolver,
        )
        self._log_event = log_event
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
        executable_tool_names: tuple[str, ...],
        max_iterations: int,
        request_timeout_sec: float,
        wall_clock_budget_sec: float,
        reasoning_effort: ReasoningEffort | None,
        automation_intent: bool,
        explicit_skill_requests: set[str] | None,
        explicit_subagent_requests: set[str] | None,
        emit_planning_progress: bool = True,
        runtime_metadata: dict[str, object] | None = None,
        trusted_runtime_context: dict[str, object] | None = None,
        approved_tool_names: tuple[str, ...] | None = None,
        approval_required_tool_names: tuple[str, ...] | None = None,
    ) -> LLMIterationResult:
        """Execute one iterative LLM loop with sequential guarded tool calls."""

        visible_tool_names = {tool.name for tool in available_tools}
        effective_allowed_tool_names = set(executable_tool_names)
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
            response = await self._complete_request_with_overflow_recovery(
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
                    call_id=call.call_id,
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
                trusted_runtime_context=trusted_runtime_context,
                allowed_tool_names=effective_allowed_tool_names,
                approved_tool_names=(
                    None
                    if approved_tool_names is None
                    else set(approved_tool_names)
                ),
                approval_required_tool_names=(
                    None
                    if approval_required_tool_names is None
                    else set(approval_required_tool_names)
                ),
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

    async def _complete_request_with_overflow_recovery(
        self,
        *,
        run_id: int,
        session_id: str,
        iteration: int,
        request: LLMRequest,
    ) -> LLMResponse:
        """Complete one provider request and retry with compacted context on overflow."""

        current_request = request
        for attempt in range(0, 3):
            response = await self._llm_request_runtime.complete_with_progress(
                run_id=run_id,
                session_id=session_id,
                iteration=iteration,
                request=current_request,
            )
            if response.error_code != "llm_context_window_exceeded":
                return response
            if attempt >= 2:
                return response

            retry_attempt = attempt + 1
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="llm.call.compaction_start",
                payload={
                    "iteration": iteration,
                    "attempt": retry_attempt,
                    "history_messages": len(current_request.history),
                    "context_chars": len(current_request.context),
                    "error_code": response.error_code,
                    "error_detail": response.error_detail,
                },
            )
            compacted = await self._request_compaction.compact_for_overflow(
                request=current_request,
                attempt=retry_attempt,
            )
            if compacted is None:
                await self._log_event(
                    run_id=run_id,
                    session_id=session_id,
                    event_type="llm.call.compaction_failed",
                    payload={
                        "iteration": iteration,
                        "attempt": retry_attempt,
                        "reason": "request_unchanged",
                    },
                )
                return response
            current_request = compacted.request
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="llm.call.compaction_done",
                payload={
                    "iteration": iteration,
                    "attempt": retry_attempt,
                    "summary_strategy": compacted.summary_strategy,
                    "summary_chars": compacted.summary_chars,
                    "preserved_recent_messages": compacted.preserved_recent_messages,
                    "history_messages_before": compacted.history_messages_before,
                    "history_messages_after": compacted.history_messages_after,
                    "context_chars_before": compacted.context_chars_before,
                    "context_chars_after": compacted.context_chars_after,
                    "compacted_history": compacted.compacted_history,
                    "compacted_context": compacted.compacted_context,
                },
            )
        return response

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
            result_payload = self._sanitize_value(
                self._summarize_tool_result_for_history(
                    tool_name=tool_call.name,
                    result=result,
                )
            )
            history.append(
                LLMMessage(
                    role="tool",
                    tool_name=tool_call.name,
                    tool_call_id=tool_call_request.call_id,
                    content=json.dumps(result_payload, ensure_ascii=True, sort_keys=True),
                )
            )

    def _summarize_tool_result_for_history(
        self,
        *,
        tool_name: str,
        result: ToolResult,
    ) -> dict[str, object]:
        """Build compact, deterministic tool-result payload for model history."""

        payload: dict[str, object] = {
            "ok": result.ok,
        }
        if result.error_code:
            payload["error_code"] = self._truncate_text_for_history(result.error_code)
        if result.reason:
            payload["reason"] = self._truncate_text_for_history(result.reason)

        raw_payload = result.payload
        if isinstance(raw_payload, dict):
            if tool_name == "bash.exec":
                payload["payload"] = self._summarize_bash_payload(raw_payload)
            else:
                payload["payload"] = self._compact_value_for_history(raw_payload)
        elif raw_payload:
            payload["payload"] = self._compact_value_for_history(raw_payload)

        if result.metadata:
            payload["metadata"] = self._compact_value_for_history(result.metadata)
        return payload

    def _summarize_bash_payload(self, payload: dict[str, object]) -> dict[str, object]:
        """Keep critical bash.exec fields while bounding stdout/stderr context."""

        compact: dict[str, object] = {}
        for field_name in (
            "cmd",
            "cwd",
            "exit_code",
            "running",
            "session_id",
            "chars_written",
            "shell",
            "login_requested",
            "login_applied",
            "stdout_truncated",
            "stderr_truncated",
        ):
            value = payload.get(field_name)
            if value in (None, "", (), [], {}):
                continue
            compact[field_name] = self._compact_value_for_history(value)

        stdout_text = self._coerce_text(payload.get("stdout"))
        stderr_text = self._coerce_text(payload.get("stderr"))
        if stdout_text:
            compact["stdout_tail"] = self._tail_lines(
                stdout_text,
                max_lines=_BASH_HISTORY_TAIL_LINES,
            )
            compact["stdout_lines_total"] = len(stdout_text.splitlines())
            compact["stdout_chars_total"] = len(stdout_text)
        if stderr_text:
            compact["stderr_tail"] = self._tail_lines(
                stderr_text,
                max_lines=_BASH_HISTORY_TAIL_LINES,
            )
            compact["stderr_lines_total"] = len(stderr_text.splitlines())
            compact["stderr_chars_total"] = len(stderr_text)
        return compact

    def _tail_lines(self, text: str, *, max_lines: int) -> list[str]:
        lines = [line.strip() for line in text.replace("\r", "\n").splitlines() if line.strip()]
        if not lines:
            return []
        return [
            self._truncate_text_for_history(line, max_chars=_BASH_HISTORY_MAX_LINE_CHARS)
            for line in lines[-max(1, max_lines) :]
        ]

    @staticmethod
    def _coerce_text(value: object) -> str:
        if isinstance(value, str):
            return value
        if value is None:
            return ""
        return str(value)

    def _compact_value_for_history(self, value: object) -> object:
        if isinstance(value, dict):
            compact_dict: dict[str, object] = {}
            items = list(value.items())
            for key, child in items[:_TOOL_HISTORY_MAX_DICT_ITEMS]:
                compact_dict[str(key)] = self._compact_value_for_history(child)
            overflow = len(items) - _TOOL_HISTORY_MAX_DICT_ITEMS
            if overflow > 0:
                compact_dict["__truncated_keys__"] = overflow
            return compact_dict
        if isinstance(value, list):
            compact_list = [
                self._compact_value_for_history(item)
                for item in value[:_TOOL_HISTORY_MAX_LIST_ITEMS]
            ]
            overflow = len(value) - _TOOL_HISTORY_MAX_LIST_ITEMS
            if overflow > 0:
                compact_list.append(f"... (+{overflow} items)")
            return compact_list
        if isinstance(value, tuple):
            return self._compact_value_for_history(list(value))
        if isinstance(value, str):
            return self._truncate_text_for_history(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self._truncate_text_for_history(str(value))

    @staticmethod
    def _truncate_text_for_history(value: str, *, max_chars: int = _TOOL_HISTORY_MAX_STRING_CHARS) -> str:
        if len(value) <= max_chars:
            return value
        return value[: max_chars - 3].rstrip() + "..."
