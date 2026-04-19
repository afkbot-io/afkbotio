"""Tests for UI auth login rate limiting internals."""

from __future__ import annotations

import pytest

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


def test_ui_auth_rate_limit_keys_are_normalized_and_bounded() -> None:
    """Remote host/username key components should be normalized and capped."""

    very_long_host = " EXAMPLE-HOST " + ("A" * 300)
    very_long_user = " USER.Name+Alias@Example.Com " + ("B" * 300)

    keys = _iter_rate_limit_keys(
        remote_host=very_long_host,
        username=very_long_user,
    )

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
