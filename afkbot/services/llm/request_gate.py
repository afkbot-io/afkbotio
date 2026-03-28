"""Shared pacing helpers for provider requests that reuse one upstream token."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass(slots=True)
class _RequestLane:
    """One shared provider/token lane with bounded concurrency and start pacing."""

    max_parallel: int
    semaphore: asyncio.Semaphore = field(init=False)
    config_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    start_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_started_at: float = 0.0

    def __post_init__(self) -> None:
        self.semaphore = asyncio.Semaphore(self.max_parallel)

    def raise_capacity(self, requested_parallel: int) -> None:
        """Increase lane capacity when a later runtime requests higher concurrency."""

        normalized_parallel = max(1, int(requested_parallel))
        if normalized_parallel <= self.max_parallel:
            return
        for _ in range(normalized_parallel - self.max_parallel):
            self.semaphore.release()
        self.max_parallel = normalized_parallel


class SharedLLMRequestGate:
    """Coordinate concurrent requests that share one upstream provider/token."""

    def __init__(self) -> None:
        self._lanes: dict[tuple[str, str], _RequestLane] = {}

    @asynccontextmanager
    async def claim(
        self,
        *,
        scope: str,
        lane_key: str,
        max_parallel: int,
        min_interval_ms: int,
    ) -> AsyncIterator[None]:
        """Reserve one shared lane slot and enforce a minimum start interval."""

        requested_parallel = max(1, int(max_parallel))
        lane = self._lanes.setdefault(
            (scope, lane_key),
            _RequestLane(max_parallel=requested_parallel),
        )
        async with lane.config_lock:
            lane.raise_capacity(requested_parallel)
        await lane.semaphore.acquire()
        try:
            interval_sec = max(0, int(min_interval_ms)) / 1000.0
            async with lane.start_lock:
                if interval_sec > 0 and lane.last_started_at > 0:
                    remaining_sec = interval_sec - (time.monotonic() - lane.last_started_at)
                    if remaining_sec > 0:
                        await asyncio.sleep(remaining_sec)
                lane.last_started_at = time.monotonic()
            yield
        finally:
            lane.semaphore.release()


_GATES_BY_SCOPE: dict[str, SharedLLMRequestGate] = {}


def get_shared_llm_request_gate(scope: str) -> SharedLLMRequestGate:
    """Return one shared request gate for the given runtime scope."""

    normalized_scope = scope.strip() or "global"
    gate = _GATES_BY_SCOPE.get(normalized_scope)
    if gate is None:
        gate = SharedLLMRequestGate()
        _GATES_BY_SCOPE[normalized_scope] = gate
    return gate


def reset_shared_llm_request_gates() -> None:
    """Reset cached request gates for tests."""

    _GATES_BY_SCOPE.clear()


def resolve_provider_request_lane_key(llm_provider: object) -> str:
    """Build a stable shared-lane key from provider identity and secret scope."""

    provider_name = getattr(llm_provider, "_provider_id", None)
    if hasattr(provider_name, "value"):
        provider_name = getattr(provider_name, "value")
    provider_text = str(provider_name or llm_provider.__class__.__name__).strip().lower()
    base_url = str(getattr(llm_provider, "_base_url", "") or "").strip().lower()
    api_key = str(getattr(llm_provider, "_api_key", "") or "").strip()
    key_digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16] if api_key else "no-key"
    return f"{provider_text}:{base_url}:{key_digest}"
