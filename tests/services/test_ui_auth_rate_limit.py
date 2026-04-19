"""Tests for UI auth login rate limiting internals."""

from __future__ import annotations

from collections import deque

import pytest
from pytest import MonkeyPatch

from afkbot.services.ui_auth.rate_limit import (
    _KEY_DIGEST_LEN,
    _MAX_RATE_LIMIT_KEY_PART_LEN,
    _SlidingWindowRateLimiter,
    _iter_rate_limit_keys,
)


@pytest.mark.asyncio
async def test_ui_auth_rate_limiter_bounds_bucket_count_for_many_unique_keys() -> None:
    """Limiter should evict old buckets and stay bounded for many unique login keys."""

    limiter = _SlidingWindowRateLimiter(max_buckets=32)

    for index in range(500):
        await limiter.add_failure(
            key=f"ui-auth:test:{index}",
            window_sec=600,
            max_attempts=5,
        )

    assert len(limiter._events) == 32
    assert "ui-auth:test:0" not in limiter._events
    assert "ui-auth:test:499" in limiter._events


@pytest.mark.asyncio
async def test_ui_auth_rate_limiter_add_failure_prunes_empty_buckets() -> None:
    """`add_failure` should not leave unrelated empty buckets behind."""

    limiter = _SlidingWindowRateLimiter(max_buckets=32)
    limiter._events["ui-auth:empty-a"] = deque()
    limiter._events["ui-auth:empty-b"] = deque()

    await limiter.add_failure(
        key="ui-auth:active",
        window_sec=600,
        max_attempts=5,
    )

    assert "ui-auth:empty-a" not in limiter._events
    assert "ui-auth:empty-b" not in limiter._events
    assert all(bucket for bucket in limiter._events.values())


@pytest.mark.asyncio
async def test_ui_auth_rate_limiter_add_failure_cleans_stale_buckets(monkeypatch: MonkeyPatch) -> None:
    """`add_failure` should trim stale buckets and keep only live keys."""

    limiter = _SlidingWindowRateLimiter(max_buckets=32)
    limiter._events["ui-auth:stale-a"] = deque([10.0])
    limiter._events["ui-auth:stale-b"] = deque([20.0])
    monkeypatch.setattr("afkbot.services.ui_auth.rate_limit.monotonic", lambda: 1000.0)

    await limiter.add_failure(
        key="ui-auth:active",
        window_sec=60,
        max_attempts=5,
    )

    assert set(limiter._events.keys()) == {"ui-auth:active"}
    assert list(limiter._events["ui-auth:active"]) == [1000.0]


def test_ui_auth_rate_limit_keys_are_normalized_and_bounded() -> None:
    """Rate-limit keys should normalize user input and cap raw key material length."""

    remote_host = "X" * 256 + " Example.COM "
    username = "  VeryLongUserName_With.Mixed-Case.And+Symbols@example.com  "

    keys = _iter_rate_limit_keys(remote_host=remote_host, username=username)

    assert len(keys) == 3
    assert all(key == key.lower() for key in keys)

    key_prefixes = ("ui-auth:remote:", "ui-auth:user:", "ui-auth:remote-user:")
    for key, prefix in zip(keys, key_prefixes, strict=True):
        suffix = key.removeprefix(prefix)
        parts = suffix.split(":")
        assert len(parts) in {2, 4}
        digests = parts[1::2]
        previews = parts[0::2]
        assert all(len(preview) <= _MAX_RATE_LIMIT_KEY_PART_LEN for preview in previews)
        assert all(len(digest) == _KEY_DIGEST_LEN for digest in digests)
