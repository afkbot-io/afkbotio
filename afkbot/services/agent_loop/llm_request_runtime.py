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
_LLM_PROGRESS_LOG_MIN_INTERVAL_SEC = 15.0
_TRANSIENT_LLM_RETRY_DELAYS_SEC = (1.0, 3.0)
_TRANSIENT_LLM_ERROR_CODES = frozenset(
    {
        "llm_provider_unavailable",
        "llm_provider_network_error",
        "llm_provider_rate_limited",
        "llm_provider_error",
        "llm_provider_response_invalid",
    }
)


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
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="llm.call.queued",
            payload={
                "iteration": iteration,
                "timeout_ms": timeout_ms,
                "available_tool_names": [tool.name for tool in request.available_tools],
                "reasoning_effort": request.reasoning_effort,
            },
        )
        async with self._request_gate.claim(
            scope=self._shared_request_scope,
            lane_key=self._request_lane_key,
            max_parallel=self._shared_request_max_parallel,
            min_interval_ms=self._shared_request_min_interval_ms,
        ):
            started_at = time.monotonic()
            last_logged_tick_at: float | None = None
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

            attempt = 1
            while True:
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
                                    "attempt": attempt,
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
                            if await self._retry_transient_response_if_needed(
                                run_id=run_id,
                                session_id=session_id,
                                iteration=iteration,
                                attempt=attempt,
                                started_at=started_at,
                                timeout_sec=timeout_sec,
                                queue_wait_ms=queue_wait_ms,
                                response=response,
                            ):
                                attempt += 1
                                last_logged_tick_at = time.monotonic()
                                break
                            elapsed_ms = int((time.monotonic() - started_at) * 1000)
                            payload: dict[str, object] = {
                                "iteration": iteration,
                                "attempt": attempt,
                                "elapsed_ms": elapsed_ms,
                                "timeout_ms": timeout_ms,
                                "queue_wait_ms": queue_wait_ms,
                                "response_kind": response.kind,
                                "error_code": response.error_code,
                                "tool_calls_count": len(response.tool_calls),
                            }
                            if response.error_code is not None:
                                payload["reason"] = response.final_message
                                payload["error_detail"] = response.error_detail
                            await self._log_event(
                                run_id=run_id,
                                session_id=session_id,
                                event_type="llm.call.done",
                                payload=payload,
                            )
                            return response

                        now = time.monotonic()
                        elapsed_ms = int((now - started_at) * 1000)
                        if (
                            last_logged_tick_at is None
                            or now - last_logged_tick_at >= _LLM_PROGRESS_LOG_MIN_INTERVAL_SEC
                        ):
                            await self._log_event(
                                run_id=run_id,
                                session_id=session_id,
                                event_type="llm.call.tick",
                                payload={
                                    "iteration": iteration,
                                    "attempt": attempt,
                                    "elapsed_ms": elapsed_ms,
                                    "timeout_ms": timeout_ms,
                                    "queue_wait_ms": queue_wait_ms,
                                },
                            )
                            last_logged_tick_at = now
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
                    error_response = LLMResponse.final(
                        "LLM provider failed before planning could complete.",
                        error_code="llm_provider_error",
                        error_detail=f"{exc.__class__.__name__}: {exc}",
                    )
                    if await self._retry_transient_response_if_needed(
                        run_id=run_id,
                        session_id=session_id,
                        iteration=iteration,
                        attempt=attempt,
                        started_at=started_at,
                        timeout_sec=timeout_sec,
                        queue_wait_ms=queue_wait_ms,
                        response=error_response,
                    ):
                        attempt += 1
                        last_logged_tick_at = time.monotonic()
                        continue
                    elapsed_ms = int((time.monotonic() - started_at) * 1000)
                    await self._log_event(
                        run_id=run_id,
                        session_id=session_id,
                        event_type="llm.call.error",
                        payload={
                            "iteration": iteration,
                            "attempt": attempt,
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

    async def _retry_transient_response_if_needed(
        self,
        *,
        run_id: int,
        session_id: str,
        iteration: int,
        attempt: int,
        started_at: float,
        timeout_sec: float,
        queue_wait_ms: int,
        response: LLMResponse,
    ) -> bool:
        """Return whether the caller should retry one transient upstream failure."""

        if response.error_code not in _TRANSIENT_LLM_ERROR_CODES:
            return False
        if attempt > len(_TRANSIENT_LLM_RETRY_DELAYS_SEC):
            return False
        delay_sec = _TRANSIENT_LLM_RETRY_DELAYS_SEC[attempt - 1]
        remaining_sec = timeout_sec - (time.monotonic() - started_at)
        effective_delay_sec = min(delay_sec, max(0.0, remaining_sec - 0.01))
        if effective_delay_sec <= 0:
            return False
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        await self._log_event(
            run_id=run_id,
            session_id=session_id,
            event_type="llm.call.retry",
            payload={
                "iteration": iteration,
                "attempt": attempt + 1,
                "elapsed_ms": elapsed_ms,
                "queue_wait_ms": queue_wait_ms,
                "error_code": response.error_code,
                "reason": response.final_message,
                "error_detail": response.error_detail,
                "delay_ms": int(effective_delay_sec * 1000),
            },
        )
        await self._raise_if_cancel_requested(run_id=run_id)
        await asyncio.sleep(effective_delay_sec)
        await self._raise_if_cancel_requested(run_id=run_id)
        return True
