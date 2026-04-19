"""Small in-process rate limiter for AFKBOT UI authentication."""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
import hashlib
from math import ceil
from time import monotonic

from afkbot.settings import Settings

_MAX_RATE_LIMIT_BUCKETS = 4096
_MAX_RATE_LIMIT_KEY_PART_LEN = 64
_KEY_DIGEST_LEN = 16


class _SlidingWindowRateLimiter:
    """Bound failed login attempts inside one moving time window per logical key."""

    def __init__(self, *, max_buckets: int = _MAX_RATE_LIMIT_BUCKETS) -> None:
        self._events: OrderedDict[str, deque[float]] = OrderedDict()
        self._max_buckets = max(max_buckets, 1)
        self._lock = asyncio.Lock()

    async def retry_after(self, *, key: str, window_sec: int, max_attempts: int) -> int | None:
        """Return retry-after seconds when one key is saturated."""

        now = monotonic()
        async with self._lock:
            self._sweep(now=now, window_sec=window_sec)
            bucket = self._events.get(key)
            if bucket is None:
                return None
            self._events.move_to_end(key)
            if len(bucket) < max(max_attempts, 1):
                return None
            return max(1, ceil(bucket[0] + max(float(window_sec), 1.0) - now))

    async def add_failure(self, *, key: str, window_sec: int, max_attempts: int) -> int | None:
        """Register one failed login attempt and return retry-after when locked."""

        now = monotonic()
        async with self._lock:
            self._sweep(now=now, window_sec=window_sec)
            bucket = self._events.get(key)
            if bucket is None:
                bucket = deque()
                self._events[key] = bucket
            else:
                self._events.move_to_end(key)
            bucket.append(now)
            self._evict_if_oversized()
            if len(bucket) < max(max_attempts, 1):
                return None
            return max(1, ceil(bucket[0] + max(float(window_sec), 1.0) - now))

    async def clear(self, *, key: str) -> None:
        """Forget counters for one logical key."""

        async with self._lock:
            self._events.pop(key, None)

    async def reset(self) -> None:
        """Clear all in-memory counters. Test helper."""

        async with self._lock:
            self._events.clear()

    def _sweep(self, *, now: float, window_sec: int) -> None:
        cutoff = now - max(float(window_sec), 1.0)
        stale_keys: list[str] = []
        for bucket_key, bucket in self._events.items():
            _trim_bucket(bucket, cutoff=cutoff)
            if not bucket:
                stale_keys.append(bucket_key)
        for bucket_key in stale_keys:
            self._events.pop(bucket_key, None)

    def _evict_if_oversized(self) -> None:
        while len(self._events) > self._max_buckets:
            self._events.popitem(last=False)


def _trim_bucket(bucket: deque[float], *, cutoff: float) -> None:
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()


_UI_AUTH_RATE_LIMITER = _SlidingWindowRateLimiter()


async def peek_ui_auth_retry_after(
    *,
    settings: Settings,
    remote_host: str | None,
    username: str | None,
) -> int | None:
    """Return retry-after if the caller is currently rate-limited."""

    window_sec = max(settings.ui_auth_login_rate_limit_window_sec, settings.ui_auth_lockout_sec)
    retry_after_values: list[int] = []
    for key in _iter_rate_limit_keys(remote_host=remote_host, username=username):
        retry_after = await _UI_AUTH_RATE_LIMITER.retry_after(
            key=key,
            window_sec=window_sec,
            max_attempts=settings.ui_auth_login_rate_limit_max_attempts,
        )
        if retry_after is not None:
            retry_after_values.append(max(retry_after, settings.ui_auth_lockout_sec))
    if not retry_after_values:
        return None
    return max(retry_after_values)


async def record_ui_auth_failure(
    *,
    settings: Settings,
    remote_host: str | None,
    username: str | None,
) -> int | None:
    """Record one failed login attempt and return retry-after when locked."""

    window_sec = max(settings.ui_auth_login_rate_limit_window_sec, settings.ui_auth_lockout_sec)
    retry_after_values: list[int] = []
    for key in _iter_rate_limit_keys(remote_host=remote_host, username=username):
        retry_after = await _UI_AUTH_RATE_LIMITER.add_failure(
            key=key,
            window_sec=window_sec,
            max_attempts=settings.ui_auth_login_rate_limit_max_attempts,
        )
        if retry_after is not None:
            retry_after_values.append(max(retry_after, settings.ui_auth_lockout_sec))
    if not retry_after_values:
        return None
    return max(retry_after_values)


async def reset_ui_auth_failures(*, remote_host: str | None, username: str | None) -> None:
    """Clear accumulated UI auth failures after a successful login."""

    for key in _iter_rate_limit_keys(remote_host=remote_host, username=username):
        await _UI_AUTH_RATE_LIMITER.clear(key=key)


async def reset_ui_auth_rate_limits() -> None:
    """Test helper to clear all accumulated UI auth failures."""

    await _UI_AUTH_RATE_LIMITER.reset()


def _iter_rate_limit_keys(*, remote_host: str | None, username: str | None) -> tuple[str, ...]:
    normalized_host = _normalize_key_part(remote_host, fallback="unknown")
    normalized_user = _normalize_key_part(username, fallback="unknown")
    return (
        f"ui-auth:remote:{normalized_host}",
        f"ui-auth:user:{normalized_user}",
        f"ui-auth:remote-user:{normalized_host}:{normalized_user}",
    )


def _normalize_key_part(value: str | None, *, fallback: str) -> str:
    normalized = " ".join(str(value or "").split()).lower()
    if not normalized:
        return fallback
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:_KEY_DIGEST_LEN]
    preview = normalized[:_MAX_RATE_LIMIT_KEY_PART_LEN]
    return f"{preview}:{digest}"
