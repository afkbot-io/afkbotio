"""Tool execution runtime for guarded tool calls and internal subagent polling."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Literal

from pydantic import ValidationError

from afkbot.services.agent_loop.channel_tool_policy import blocked_tool_result_for_runtime
from afkbot.services.agent_loop.sensitive_tool_policy import blocked_tool_result
from afkbot.models.profile_policy import ProfilePolicy
from afkbot.services.agent_loop.safety_policy import SafetyPolicy
from afkbot.services.agent_loop.security_guard import SecurityGuard
from afkbot.services.agent_loop.tool_invocation_gates import ToolInvocationGuards
from afkbot.services.policy import PolicyEngine, PolicyViolationError
from afkbot.services.tools.base import ToolCall, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParametersValidationError
from afkbot.services.tools.registry import ToolRegistry

AsyncLogEvent = Callable[..., Awaitable[None]]
AsyncCancelCheck = Callable[..., Awaitable[None]]
SanitizeValue = Callable[[object], object]
NormalizeParams = Callable[[object], dict[str, object]]
BuildToolLogPayload = Callable[..., dict[str, object]]
SanitizeText = Callable[[str], str]


class ToolExecutionRuntime:
    """Execute guarded tool calls and persist deterministic tool call/result events."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry | None,
        actor: Literal["main", "subagent"] = "main",
        policy_engine: PolicyEngine,
        security_guard: SecurityGuard,
        safety_policy: SafetyPolicy,
        tool_invocation_gates: ToolInvocationGuards,
        tool_timeout_default_sec: int,
        tool_timeout_max_sec: int,
        log_event: AsyncLogEvent,
        raise_if_cancel_requested: AsyncCancelCheck,
        sanitize: SanitizeText,
        sanitize_value: SanitizeValue,
        to_params_dict: NormalizeParams,
        tool_log_payload: BuildToolLogPayload,
    ) -> None:
        self._tool_registry = tool_registry
        self._actor = actor
        self._policy_engine = policy_engine
        self._security_guard = security_guard
        self._safety_policy = safety_policy
        self._tool_invocation_gates = tool_invocation_gates
        self._tool_timeout_default_sec = tool_timeout_default_sec
        self._tool_timeout_max_sec = tool_timeout_max_sec
        self._log_event = log_event
        self._raise_if_cancel_requested = raise_if_cancel_requested
        self._sanitize = sanitize
        self._sanitize_value = sanitize_value
        self._to_params_dict = to_params_dict
        self._tool_log_payload = tool_log_payload

    async def execute_requested_tool_calls(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        tool_calls: list[ToolCall],
        policy: ProfilePolicy,
        automation_intent: bool,
        explicit_skill_requests: set[str] | None,
        explicit_subagent_requests: set[str] | None,
        allow_confirmation_markers: bool,
        runtime_metadata: dict[str, object] | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> list[ToolResult]:
        """Execute requested tool calls sequentially with logging and pre-execution guards."""

        ctx = ToolContext(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            actor=self._actor,
            runtime_metadata=runtime_metadata,
        )
        results: list[ToolResult] = []
        explicit_skills = {name.strip() for name in (explicit_skill_requests or set()) if name.strip()}
        explicit_subagents = {
            name.strip() for name in (explicit_subagent_requests or set()) if name.strip()
        }
        for tool_call in tool_calls:
            await self._raise_if_cancel_requested(run_id=run_id)
            execution_name = tool_call.name.strip()
            execution_params = self._to_params_dict(tool_call.params)
            confirmed, confirmation_question_id = self._safety_policy.extract_confirmation_ack(
                execution_params,
            )
            if not allow_confirmation_markers:
                confirmed = False
                confirmation_question_id = None
            execution_params["profile_id"] = profile_id
            execution_params["profile_key"] = profile_id
            guarded = self._security_guard.guard_tool_call(
                call=ToolCall(name=execution_name, params=execution_params),
            )
            sanitized_name = self._sanitize(guarded.log_call.name)
            sanitized_params = self._to_params_dict(self._sanitize_value(guarded.log_call.params))
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="tool.call",
                payload=self._tool_log_payload(
                    tool_name=sanitized_name,
                    payload={"name": sanitized_name, "params": sanitized_params},
                ),
            )
            tool_ctx = replace(
                ctx,
                progress_callback=self._build_tool_progress_callback(
                    run_id=run_id,
                    session_id=session_id,
                    tool_name=sanitized_name,
                ),
            )
            result = await self._execute_single_tool_call(
                run_id=run_id,
                session_id=session_id,
                profile_id=profile_id,
                ctx=tool_ctx,
                execution_name=execution_name,
                execution_params=execution_params,
                guarded_call=guarded.execution_call,
                guarded_allowed=guarded.allow,
                guarded_error_code=guarded.error_code,
                guarded_reason=guarded.blocked_reason,
                policy=policy,
                automation_intent=automation_intent,
                explicit_skills=explicit_skills,
                explicit_subagents=explicit_subagents,
                confirmed=confirmed,
                confirmation_question_id=confirmation_question_id,
                allowed_tool_names=allowed_tool_names,
            )
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="tool.result",
                payload=self._tool_log_payload(
                    tool_name=sanitized_name,
                    payload={
                        "name": sanitized_name,
                        "result": self._sanitize_value(result.model_dump()),
                    },
                ),
            )
            results.append(result)
            await self._raise_if_cancel_requested(run_id=run_id)
        return results

    async def execute_tool_call(self, *, tool_call: ToolCall, ctx: ToolContext) -> ToolResult:
        """Execute one tool call and convert failures to canonical tool errors."""

        sensitive_block = blocked_tool_result(
            tool_name=tool_call.name,
            runtime_metadata=ctx.runtime_metadata,
        )
        if sensitive_block is not None:
            return sensitive_block
        channel_profile_block = blocked_tool_result_for_runtime(
            tool_name=tool_call.name,
            runtime_metadata=ctx.runtime_metadata,
        )
        if channel_profile_block is not None:
            return channel_profile_block
        if self._tool_registry is None:
            return ToolResult.error(
                error_code="tool_registry_unavailable",
                reason="Tool registry is not configured for this loop.",
            )

        tool = self._tool_registry.get(tool_call.name)
        if tool is None:
            return ToolResult.error(
                error_code="tool_not_found",
                reason=f"Tool not found: {tool_call.name}",
            )

        try:
            params = tool.parse_params(
                tool_call.params,
                default_timeout_sec=self._tool_timeout_default_sec,
                max_timeout_sec=self._tool_timeout_max_sec,
            )
        except ToolParametersValidationError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata=exc.metadata,
            )
        except (ValidationError, ValueError) as exc:
            return ToolResult.error(
                error_code="tool_params_invalid",
                reason=str(exc),
            )

        try:
            return await asyncio.wait_for(
                tool.execute(ctx, params),
                timeout=float(params.timeout_sec),
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            return ToolResult.error(
                error_code="tool_execution_failed",
                reason=f"Tool timed out after {params.timeout_sec} seconds",
            )
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult.error(
                error_code="tool_execution_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )

    async def await_subagent_result_after_run(
        self,
        *,
        run_id: int,
        session_id: str,
        ctx: ToolContext,
        run_result: ToolResult,
    ) -> ToolResult:
        """Execute wait/result cycle after successful subagent.run call."""

        payload = run_result.payload if isinstance(run_result.payload, dict) else {}
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            return run_result

        timeout_sec_raw = payload.get("timeout_sec")
        try:
            total_timeout_sec = int(str(timeout_sec_raw)) if timeout_sec_raw is not None else 900
        except (TypeError, ValueError):
            total_timeout_sec = 900
        total_timeout_sec = max(1, total_timeout_sec)

        deadline = asyncio.get_running_loop().time() + float(total_timeout_sec)
        wait_slice_sec = 30

        while True:
            await self._raise_if_cancel_requested(run_id=run_id)
            now = asyncio.get_running_loop().time()
            remaining = deadline - now
            if remaining <= 0:
                return ToolResult.error(
                    error_code="subagent_timeout",
                    reason=f"Subagent timed out after {total_timeout_sec} seconds",
                    metadata={"task_id": task_id},
                )

            wait_call = ToolCall(
                name="subagent.wait",
                params={
                    "task_id": task_id,
                    "profile_id": ctx.profile_id,
                    "profile_key": ctx.profile_id,
                    "timeout_sec": max(1, min(wait_slice_sec, int(remaining))),
                },
            )
            wait_result = await self._execute_internal_tool_with_logging(
                run_id=run_id,
                session_id=session_id,
                ctx=ctx,
                tool_call=wait_call,
            )
            if not wait_result.ok:
                return wait_result
            wait_payload = wait_result.payload if isinstance(wait_result.payload, dict) else {}
            if bool(wait_payload.get("done")):
                break

        await self._raise_if_cancel_requested(run_id=run_id)
        result_call = ToolCall(
            name="subagent.result",
            params={
                "task_id": task_id,
                "profile_id": ctx.profile_id,
                "profile_key": ctx.profile_id,
            },
        )
        result = await self._execute_internal_tool_with_logging(
            run_id=run_id,
            session_id=session_id,
            ctx=ctx,
            tool_call=result_call,
        )
        if not result.ok:
            return result

        result_payload = result.payload if isinstance(result.payload, dict) else {}
        merged_payload = {
            **payload,
            "status": result_payload.get("status"),
            "output": result_payload.get("output"),
            "result": result_payload,
        }
        return ToolResult(ok=True, payload=merged_payload)

    async def _execute_single_tool_call(
        self,
        *,
        run_id: int,
        session_id: str,
        profile_id: str,
        ctx: ToolContext,
        execution_name: str,
        execution_params: dict[str, object],
        guarded_call: ToolCall,
        guarded_allowed: bool,
        guarded_error_code: str | None,
        guarded_reason: str | None,
        policy: ProfilePolicy,
        automation_intent: bool,
        explicit_skills: set[str],
        explicit_subagents: set[str],
        confirmed: bool,
        confirmation_question_id: str | None,
        allowed_tool_names: set[str] | None,
    ) -> ToolResult:
        """Execute one already-logged tool call through all runtime guards."""

        if not guarded_allowed:
            return ToolResult.error(
                error_code=guarded_error_code or "security_secure_input_required",
                reason=guarded_reason or "Secret-like tool call blocked",
            )
        if allowed_tool_names is not None and execution_name not in allowed_tool_names:
            return ToolResult.error(
                error_code="tool_not_allowed_in_turn",
                reason=f"Tool not available in current turn: {execution_name}",
            )
        if execution_name == "subagent.run":
            requested_subagent = str(execution_params.get("subagent_name") or "").strip()
            subagent_intent_result = self._tool_invocation_gates.subagent_intent_mismatch_result(
                requested_subagent=requested_subagent,
                explicit_skills=explicit_skills,
                explicit_subagents=explicit_subagents,
            )
            if subagent_intent_result is not None:
                return subagent_intent_result
        tool = None if self._tool_registry is None else self._tool_registry.get(execution_name)
        approval_params = (
            execution_params
            if tool is None
            else tool.policy_params(
                execution_params,
                ctx=ctx,
            )
        )
        automation_intent_result = self._tool_invocation_gates.automation_intent_required_result(
            tool_name=execution_name,
            automation_intent=automation_intent,
        )
        if automation_intent_result is not None:
            return automation_intent_result

        approval_result = self._safety_policy.approval_required_result(
            policy=policy,
            tool_name=execution_name,
            params=approval_params,
            confirmed=confirmed,
            question_id=confirmation_question_id,
        )
        if approval_result is not None:
            return approval_result

        skill_gate_result = await self._tool_invocation_gates.skill_gate_result(
            run_id=run_id,
            session_id=session_id,
            profile_id=profile_id,
            tool_name=execution_name,
            params=execution_params,
        )
        if skill_gate_result is not None:
            return skill_gate_result

        try:
            self._policy_engine.ensure_tool_call_allowed(
                policy=policy,
                tool_name=execution_name,
                params=approval_params,
            )
            result = await self.execute_tool_call(
                tool_call=guarded_call,
                ctx=ctx,
            )
            if result.ok and execution_name == "subagent.run":
                return await self.await_subagent_result_after_run(
                    run_id=run_id,
                    session_id=session_id,
                    ctx=ctx,
                    run_result=result,
                )
            return result
        except PolicyViolationError as exc:
            return ToolResult.error(
                error_code="profile_policy_violation",
                reason=exc.reason,
            )

    async def _execute_internal_tool_with_logging(
        self,
        *,
        run_id: int,
        session_id: str,
        ctx: ToolContext,
        tool_call: ToolCall,
    ) -> ToolResult:
        """Execute internal helper tool call with standard tool call/result logs."""

        sanitized_name = self._sanitize(tool_call.name)
        sanitized_params = self._to_params_dict(self._sanitize_value(tool_call.params))
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="tool.call",
            payload=self._tool_log_payload(
                tool_name=sanitized_name,
                payload={"name": sanitized_name, "params": sanitized_params},
            ),
        )
        result = await self.execute_tool_call(tool_call=tool_call, ctx=ctx)
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="tool.result",
            payload=self._tool_log_payload(
                tool_name=sanitized_name,
                payload={
                    "name": sanitized_name,
                    "result": self._sanitize_value(result.model_dump()),
                },
            ),
        )
        return result

    def _build_tool_progress_callback(
        self,
        *,
        run_id: int,
        session_id: str,
        tool_name: str,
    ) -> Callable[[dict[str, object]], Awaitable[None]]:
        """Build one sanitized progress logger for the currently running tool call."""

        async def _emit(payload: dict[str, object]) -> None:
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="tool.progress",
                payload=self._tool_log_payload(
                    tool_name=tool_name,
                    payload={
                        "name": tool_name,
                        "progress": self._sanitize_value(payload),
                    },
                ),
            )

        return _emit
