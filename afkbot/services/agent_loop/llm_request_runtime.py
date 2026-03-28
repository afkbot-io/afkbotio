"""LLM request runtime with timeout, heartbeat progress, and deterministic fallback errors."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

from afkbot.services.llm.contracts import LLMProvider, LLMRequest, LLMResponse
from afkbot.services.llm.request_gate import (
    SharedLLMRequestGate,
    get_shared_llm_request_gate,
    resolve_provider_request_lane_key,
)
from afkbot.services.llm_timeout_policy import resolve_llm_request_timeout_sec

AsyncLogEvent = Callable[..., Awaitable[None]]
AsyncCancelCheck = Callable[..., Awaitable[None]]

_LLM_PROGRESS_TICK_SEC = 3.0


class LLMRequestRuntime:
    """Complete one provider request with timeout handling and progress events."""

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        llm_request_timeout_sec: float,
        log_event: AsyncLogEvent,
        raise_if_cancel_requested: AsyncCancelCheck,
        shared_request_scope: str = "global",
        shared_request_max_parallel: int = 1,
        shared_request_min_interval_ms: int = 0,
        request_gate: SharedLLMRequestGate | None = None,
    ) -> None:
        self._llm_provider = llm_provider
        self._llm_request_timeout_sec = max(0.01, float(llm_request_timeout_sec))
        self._log_event = log_event
        self._raise_if_cancel_requested = raise_if_cancel_requested
        self._shared_request_scope = shared_request_scope.strip() or "global"
        self._shared_request_max_parallel = max(1, int(shared_request_max_parallel))
        self._shared_request_min_interval_ms = max(0, int(shared_request_min_interval_ms))
        self._request_gate = request_gate or get_shared_llm_request_gate(self._shared_request_scope)
        self._request_lane_key = resolve_provider_request_lane_key(llm_provider)

    async def complete_with_progress(
        self,
        *,
        run_id: int,
        session_id: str,
        iteration: int,
        request: LLMRequest,
    ) -> LLMResponse:
        """Complete one LLM request with timeout and periodic progress events."""

        timeout_sec = resolve_llm_request_timeout_sec(
            request.request_timeout_sec,
            fallback_sec=self._llm_request_timeout_sec,
        )
        timeout_ms = int(timeout_sec * 1000)
        queued_at = time.monotonic()
        async with self._request_gate.claim(
            scope=self._shared_request_scope,
            lane_key=self._request_lane_key,
            max_parallel=self._shared_request_max_parallel,
            min_interval_ms=self._shared_request_min_interval_ms,
        ):
            started_at = time.monotonic()
            queue_wait_ms = int((started_at - queued_at) * 1000)
            await self._log_event(
                run_id=run_id,
                session_id=session_id,
                event_type="llm.call.start",
                payload={
                    "iteration": iteration,
                    "timeout_ms": timeout_ms,
                    "queue_wait_ms": queue_wait_ms,
                    "available_tool_names": [tool.name for tool in request.available_tools],
                    "reasoning_effort": request.reasoning_effort,
                },
            )

            task = asyncio.create_task(self._llm_provider.complete(request))
            try:
                while True:
                    elapsed_sec = time.monotonic() - started_at
                    if elapsed_sec >= timeout_sec:
                        task.cancel()
                        with suppress(asyncio.CancelledError):
                            await task
                        elapsed_ms = int((time.monotonic() - started_at) * 1000)
                        await self._log_event(
                            run_id=run_id,
                            session_id=session_id,
                            event_type="llm.call.timeout",
                            payload={
                                "iteration": iteration,
                                "elapsed_ms": elapsed_ms,
                                "timeout_ms": timeout_ms,
                                "queue_wait_ms": queue_wait_ms,
                                "error_code": "llm_timeout",
                            },
                        )
                        return LLMResponse.final(
                            "LLM request timed out before planning could complete.",
                            error_code="llm_timeout",
                        )

                    wait_sec = min(_LLM_PROGRESS_TICK_SEC, timeout_sec - elapsed_sec)
                    done, _ = await asyncio.wait({task}, timeout=wait_sec)
                    if done:
                        response = task.result()
                        elapsed_ms = int((time.monotonic() - started_at) * 1000)
                        await self._log_event(
                            run_id=run_id,
                            session_id=session_id,
                            event_type="llm.call.done",
                            payload={
                                "iteration": iteration,
                                "elapsed_ms": elapsed_ms,
                                "timeout_ms": timeout_ms,
                                "queue_wait_ms": queue_wait_ms,
                                "response_kind": response.kind,
                                "error_code": response.error_code,
                                "tool_calls_count": len(response.tool_calls),
                            },
                        )
                        return response

                    elapsed_ms = int((time.monotonic() - started_at) * 1000)
                    await self._log_event(
                        run_id=run_id,
                        session_id=session_id,
                        event_type="llm.call.tick",
                        payload={
                            "iteration": iteration,
                            "elapsed_ms": elapsed_ms,
                            "timeout_ms": timeout_ms,
                            "queue_wait_ms": queue_wait_ms,
                        },
                    )
                    await self._raise_if_cancel_requested(run_id=run_id)
            except asyncio.CancelledError:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise
            except Exception as exc:  # pragma: no cover - defensive fallback
                task.cancel()
                with suppress(BaseException):
                    await task
                elapsed_ms = int((time.monotonic() - started_at) * 1000)
                await self._log_event(
                    run_id=run_id,
                    session_id=session_id,
                    event_type="llm.call.error",
                    payload={
                        "iteration": iteration,
                        "elapsed_ms": elapsed_ms,
                        "queue_wait_ms": queue_wait_ms,
                        "error_code": "llm_provider_error",
                        "reason": f"{exc.__class__.__name__}: {exc}",
                    },
                )
                return LLMResponse.final(
                    "LLM provider failed before planning could complete.",
                    error_code="llm_provider_error",
                )
