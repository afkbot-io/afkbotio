"""Tests for the LLM request runtime."""

from __future__ import annotations

import asyncio
import time

import pytest

from afkbot.services.agent_loop.llm_request_runtime import LLMRequestRuntime
from afkbot.services.llm import BaseLLMProvider, LLMRequest, LLMResponse, MockLLMProvider
from afkbot.services.llm.request_gate import reset_shared_llm_request_gates


@pytest.fixture(autouse=True)
def _reset_request_gates() -> None:
    reset_shared_llm_request_gates()
    yield
    reset_shared_llm_request_gates()


class _SlowProvider(BaseLLMProvider):
    def __init__(self, *, sleep_sec: float, response: LLMResponse) -> None:
        self._sleep_sec = sleep_sec
        self._response = response

    async def complete(self, request: LLMRequest) -> LLMResponse:
        _ = request
        await asyncio.sleep(self._sleep_sec)
        return self._response


class _ErrorProvider(BaseLLMProvider):
    async def complete(self, request: LLMRequest) -> LLMResponse:
        _ = request
        raise RuntimeError("provider exploded")


class _SharedLaneProvider(BaseLLMProvider):
    def __init__(
        self,
        *,
        sleep_sec: float,
        calls: list[float],
        active_counts: list[int],
        active_ref: list[int],
    ) -> None:
        self._sleep_sec = sleep_sec
        self._calls = calls
        self._active_counts = active_counts
        self._active_ref = active_ref
        self._provider_id = "openrouter"
        self._base_url = "https://api.example.test/v1"
        self._api_key = "shared-token"

    async def complete(self, request: LLMRequest) -> LLMResponse:
        _ = request
        self._active_ref[0] += 1
        self._active_counts.append(self._active_ref[0])
        self._calls.append(time.monotonic())
        try:
            await asyncio.sleep(self._sleep_sec)
            return LLMResponse.final("ok")
        finally:
            self._active_ref[0] -= 1


def _request() -> LLMRequest:
    return LLMRequest(
        profile_id="default",
        session_id="s-1",
        context="ctx",
        history=[],
        available_tools=(),
    )


async def _noop_cancel_check(**_: object) -> None:
    return None


async def _collect_log_event(storage: list[dict[str, object]], **kwargs: object) -> None:
    storage.append(dict(kwargs))


async def test_llm_request_runtime_logs_successful_completion() -> None:
    """Successful provider responses should emit start and done events."""

    events: list[dict[str, object]] = []
    runtime = LLMRequestRuntime(
        llm_provider=MockLLMProvider([LLMResponse.final("ok")]),
        llm_request_timeout_sec=1.0,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        raise_if_cancel_requested=_noop_cancel_check,
    )

    response = await runtime.complete_with_progress(
        run_id=1,
        session_id="s-1",
        iteration=1,
        request=_request(),
    )

    assert response.kind == "final"
    assert response.final_message == "ok"
    assert [item["event_type"] for item in events] == ["llm.call.start", "llm.call.done"]


async def test_llm_request_runtime_returns_timeout_fallback() -> None:
    """Slow provider responses should return a deterministic timeout final response."""

    events: list[dict[str, object]] = []
    runtime = LLMRequestRuntime(
        llm_provider=_SlowProvider(
            sleep_sec=0.2,
            response=LLMResponse.final("late"),
        ),
        llm_request_timeout_sec=0.05,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        raise_if_cancel_requested=_noop_cancel_check,
    )

    response = await runtime.complete_with_progress(
        run_id=2,
        session_id="s-2",
        iteration=1,
        request=_request(),
    )

    assert response.kind == "final"
    assert response.error_code == "llm_timeout"
    assert response.final_message == "LLM request timed out before planning could complete."
    assert [item["event_type"] for item in events] == [
        "llm.call.start",
        "llm.call.tick",
        "llm.call.timeout",
    ]


async def test_llm_request_runtime_returns_provider_error_fallback() -> None:
    """Unexpected provider exceptions should become deterministic error responses."""

    events: list[dict[str, object]] = []
    runtime = LLMRequestRuntime(
        llm_provider=_ErrorProvider(),
        llm_request_timeout_sec=1.0,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        raise_if_cancel_requested=_noop_cancel_check,
    )

    response = await runtime.complete_with_progress(
        run_id=3,
        session_id="s-3",
        iteration=1,
        request=_request(),
    )

    assert response.kind == "final"
    assert response.error_code == "llm_provider_error"
    assert response.final_message == "LLM provider failed before planning could complete."
    assert [item["event_type"] for item in events] == ["llm.call.start", "llm.call.error"]


async def test_llm_request_runtime_honors_request_timeout_override() -> None:
    """Per-request timeout should override runtime default when thinking level raises budget."""

    events: list[dict[str, object]] = []
    runtime = LLMRequestRuntime(
        llm_provider=_SlowProvider(
            sleep_sec=0.1,
            response=LLMResponse.final("slow ok"),
        ),
        llm_request_timeout_sec=0.05,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        raise_if_cancel_requested=_noop_cancel_check,
    )

    response = await runtime.complete_with_progress(
        run_id=4,
        session_id="s-4",
        iteration=1,
        request=_request().model_copy(update={"request_timeout_sec": 0.2}),
    )

    assert response.kind == "final"
    assert response.final_message == "slow ok"
    assert [item["event_type"] for item in events] == ["llm.call.start", "llm.call.done"]


async def test_llm_request_runtime_caps_oversized_request_timeout_override() -> None:
    """Per-request timeout should stay bounded by the shared 30-minute runtime cap."""

    # Arrange
    events: list[dict[str, object]] = []
    runtime = LLMRequestRuntime(
        llm_provider=MockLLMProvider([LLMResponse.final("ok")]),
        llm_request_timeout_sec=1.0,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        raise_if_cancel_requested=_noop_cancel_check,
    )

    # Act
    response = await runtime.complete_with_progress(
        run_id=5,
        session_id="s-5",
        iteration=1,
        request=_request().model_copy(update={"request_timeout_sec": 9999.0}),
    )

    # Assert
    assert response.kind == "final"
    assert response.final_message == "ok"
    assert [item["event_type"] for item in events] == ["llm.call.start", "llm.call.done"]
    start_payload = events[0]["payload"]
    assert isinstance(start_payload, dict)
    assert start_payload["timeout_ms"] == 1_800_000


async def test_llm_request_runtime_serializes_shared_provider_lane() -> None:
    """Concurrent runtimes with one shared provider token should not overlap upstream calls."""

    events: list[dict[str, object]] = []
    calls: list[float] = []
    active_counts: list[int] = []
    active_ref = [0]

    runtime_one = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.03,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=1,
        shared_request_min_interval_ms=0,
    )
    runtime_two = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.03,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=lambda **kwargs: _collect_log_event(events, **kwargs),
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=1,
        shared_request_min_interval_ms=0,
    )

    await asyncio.gather(
        runtime_one.complete_with_progress(run_id=10, session_id="s-10", iteration=1, request=_request()),
        runtime_two.complete_with_progress(run_id=11, session_id="s-11", iteration=1, request=_request()),
    )

    assert max(active_counts) == 1
    assert len(calls) == 2


async def test_llm_request_runtime_allows_parallel_requests_when_lane_capacity_is_two() -> None:
    """Concurrent runtimes should overlap when the shared lane permits two in-flight calls."""

    # Arrange
    calls: list[float] = []
    active_counts: list[int] = []
    active_ref = [0]
    runtime_one = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.03,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=_noop_cancel_check,
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=2,
        shared_request_min_interval_ms=0,
    )
    runtime_two = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.03,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=_noop_cancel_check,
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=2,
        shared_request_min_interval_ms=0,
    )

    # Act
    await asyncio.gather(
        runtime_one.complete_with_progress(run_id=30, session_id="s-30", iteration=1, request=_request()),
        runtime_two.complete_with_progress(run_id=31, session_id="s-31", iteration=1, request=_request()),
    )

    # Assert
    assert len(calls) == 2
    assert max(active_counts) == 2


async def test_llm_request_runtime_upgrades_existing_lane_capacity() -> None:
    """Later runtimes should be able to raise shared-lane concurrency for the same provider."""

    # Arrange
    calls: list[float] = []
    active_counts: list[int] = []
    active_ref = [0]
    serial_runtime = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.03,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=_noop_cancel_check,
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=1,
        shared_request_min_interval_ms=0,
    )
    parallel_runtime_one = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.03,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=_noop_cancel_check,
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=2,
        shared_request_min_interval_ms=0,
    )
    parallel_runtime_two = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.03,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=_noop_cancel_check,
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=2,
        shared_request_min_interval_ms=0,
    )

    await serial_runtime.complete_with_progress(
        run_id=40,
        session_id="s-40",
        iteration=1,
        request=_request(),
    )

    # Act
    await asyncio.gather(
        parallel_runtime_one.complete_with_progress(
            run_id=41,
            session_id="s-41",
            iteration=1,
            request=_request(),
        ),
        parallel_runtime_two.complete_with_progress(
            run_id=42,
            session_id="s-42",
            iteration=1,
            request=_request(),
        ),
    )

    # Assert
    assert len(calls) == 3
    assert max(active_counts) == 2


async def test_llm_request_runtime_enforces_shared_start_interval() -> None:
    """Shared provider lane should delay the next request start by the configured interval."""

    calls: list[float] = []
    active_counts: list[int] = []
    active_ref = [0]
    runtime_one = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.0,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=_noop_cancel_check,
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=1,
        shared_request_min_interval_ms=40,
    )
    runtime_two = LLMRequestRuntime(
        llm_provider=_SharedLaneProvider(
            sleep_sec=0.0,
            calls=calls,
            active_counts=active_counts,
            active_ref=active_ref,
        ),
        llm_request_timeout_sec=1.0,
        log_event=_noop_cancel_check,
        raise_if_cancel_requested=_noop_cancel_check,
        shared_request_scope="tests",
        shared_request_max_parallel=1,
        shared_request_min_interval_ms=40,
    )

    await asyncio.gather(
        runtime_one.complete_with_progress(run_id=20, session_id="s-20", iteration=1, request=_request()),
        runtime_two.complete_with_progress(run_id=21, session_id="s-21", iteration=1, request=_request()),
    )

    assert len(calls) == 2
    assert calls[1] - calls[0] >= 0.035
