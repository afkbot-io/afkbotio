"""Small in-process rate limiter for public connect endpoints."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from math import ceil
from time import monotonic

from afkbot.services.connect.contracts import ConnectServiceError
from afkbot.services.connect.helpers import hash_token
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class _ConnectRateLimitPolicy:
    window_sec: int
    max_attempts: int


class _SlidingWindowRateLimiter:
    """Bound requests inside one moving time window per logical key."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, *, key: str, window_sec: int, max_attempts: int) -> int | None:
        """Register one event or return retry-after seconds when the key is saturated."""

        now = monotonic()
        async with self._lock:
            bucket = self._events.setdefault(key, deque())
            cutoff = now - max(float(window_sec), 1.0)
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= max(max_attempts, 1):
                retry_after = max(1, ceil(bucket[0] + max(float(window_sec), 1.0) - now))
                return retry_after
            bucket.append(now)
            if not bucket:
                self._events.pop(key, None)
            return None

    async def reset(self) -> None:
        """Clear all in-memory counters. Test-only helper."""

        async with self._lock:
            self._events.clear()


_CONNECT_RATE_LIMITER = _SlidingWindowRateLimiter()


async def enforce_connect_rate_limit(
    *,
    settings: Settings,
    scope: str,
    remote_host: str | None,
    token_value: str | None = None,
) -> None:
    """Fail closed with deterministic 429 when one connect surface is abused."""

    if not settings.connect_rate_limit_enabled:
        return
    policy = _policy_for_scope(settings=settings, scope=scope)
    normalized_host = _normalize_remote_host(remote_host)
    retry_after_values: list[int] = []
    for key in _iter_rate_limit_keys(
        scope=scope,
        remote_host=normalized_host,
        token_value=token_value,
    ):
        retry_after = await _CONNECT_RATE_LIMITER.check(
            key=key,
            window_sec=policy.window_sec,
            max_attempts=policy.max_attempts,
        )
        if retry_after is not None:
            retry_after_values.append(retry_after)
    if not retry_after_values:
        return
    retry_after_sec = max(retry_after_values)
    raise ConnectServiceError(
        error_code="connect_rate_limited",
        reason=(
            f"Too many connect {scope} requests. "
            f"Retry after {retry_after_sec} seconds."
        ),
        retry_after_sec=retry_after_sec,
    )


async def reset_connect_rate_limits() -> None:
    """Clear connect endpoint rate-limit state. Test helper."""

    await _CONNECT_RATE_LIMITER.reset()


def _policy_for_scope(*, settings: Settings, scope: str) -> _ConnectRateLimitPolicy:
    normalized = scope.strip().lower()
    if normalized == "claim":
        return _ConnectRateLimitPolicy(
            window_sec=settings.connect_claim_rate_limit_window_sec,
            max_attempts=settings.connect_claim_rate_limit_max_attempts,
        )
    if normalized == "refresh":
        return _ConnectRateLimitPolicy(
            window_sec=settings.connect_refresh_rate_limit_window_sec,
            max_attempts=settings.connect_refresh_rate_limit_max_attempts,
        )
    if normalized == "revoke":
        return _ConnectRateLimitPolicy(
            window_sec=settings.connect_revoke_rate_limit_window_sec,
            max_attempts=settings.connect_revoke_rate_limit_max_attempts,
        )
    raise ValueError(f"Unsupported connect rate-limit scope: {scope}")


def _normalize_remote_host(remote_host: str | None) -> str:
    normalized = str(remote_host or "").strip()
    return normalized or "unknown"


def _iter_rate_limit_keys(
    *,
    scope: str,
    remote_host: str,
    token_value: str | None,
) -> tuple[str, ...]:
    token_fingerprint = _fingerprint_token(token_value)
    if token_fingerprint is None:
        return (f"{scope}:remote:{remote_host}",)
    return (
        f"{scope}:remote:{remote_host}",
        f"{scope}:token:{token_fingerprint}",
    )


def _fingerprint_token(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    return hash_token(normalized)[:16]
