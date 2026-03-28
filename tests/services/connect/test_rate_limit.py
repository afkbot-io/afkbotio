"""Tests for connect endpoint rate limiting."""

from __future__ import annotations

import pytest

from afkbot.services.connect.contracts import ConnectServiceError
from afkbot.services.connect.rate_limit import enforce_connect_rate_limit, reset_connect_rate_limits
from afkbot.settings import Settings


@pytest.mark.asyncio
async def test_connect_rate_limit_blocks_after_budget() -> None:
    """Per-scope limiter should return deterministic 429-style error after the configured budget."""

    await reset_connect_rate_limits()
    settings = Settings(
        connect_rate_limit_enabled=True,
        connect_claim_rate_limit_window_sec=60,
        connect_claim_rate_limit_max_attempts=1,
    )

    await enforce_connect_rate_limit(
        settings=settings,
        scope="claim",
        remote_host="127.0.0.1",
        token_value="claim-1",
    )

    with pytest.raises(ConnectServiceError) as exc_info:
        await enforce_connect_rate_limit(
            settings=settings,
            scope="claim",
            remote_host="127.0.0.1",
            token_value="claim-1",
        )

    assert exc_info.value.error_code == "connect_rate_limited"
    assert exc_info.value.retry_after_sec is not None
    assert exc_info.value.retry_after_sec >= 1


@pytest.mark.asyncio
async def test_connect_rate_limit_can_be_disabled() -> None:
    """Disabled limiter should not reject repeated connect requests."""

    await reset_connect_rate_limits()
    settings = Settings(
        connect_rate_limit_enabled=False,
        connect_refresh_rate_limit_window_sec=60,
        connect_refresh_rate_limit_max_attempts=1,
    )

    await enforce_connect_rate_limit(
        settings=settings,
        scope="refresh",
        remote_host="127.0.0.1",
        token_value="ref-1",
    )
    await enforce_connect_rate_limit(
        settings=settings,
        scope="refresh",
        remote_host="127.0.0.1",
        token_value="ref-1",
    )
