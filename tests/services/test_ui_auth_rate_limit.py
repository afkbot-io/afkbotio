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

    remote_key, user_key, remote_user_key = keys

    assert remote_key.startswith("ui-auth:remote:")
    remote_suffix = remote_key.removeprefix("ui-auth:remote:")
    remote_preview, remote_digest = remote_suffix.rsplit(":", 1)
    assert len(remote_preview) <= _MAX_RATE_LIMIT_KEY_PART_LEN
    assert len(remote_digest) == _KEY_DIGEST_LEN

    assert user_key.startswith("ui-auth:user:")
    user_suffix = user_key.removeprefix("ui-auth:user:")
    user_preview, user_digest = user_suffix.rsplit(":", 1)
    assert len(user_preview) <= _MAX_RATE_LIMIT_KEY_PART_LEN
    assert len(user_digest) == _KEY_DIGEST_LEN

    assert remote_user_key.startswith("ui-auth:remote-user:")
    remote_user_suffix = remote_user_key.removeprefix("ui-auth:remote-user:")
    assert remote_user_suffix == f"{remote_suffix}:{user_suffix}"
