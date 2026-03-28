"""Shared fixtures for env-gated live integration tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _require_live_enable(request: pytest.FixtureRequest) -> None:
    """Skip tests marked as live unless explicit opt-in env var is enabled."""

    if request.node.get_closest_marker("live") is None:
        return
    if os.getenv("AFKBOT_LIVE_ENABLE", "0").strip() == "1":
        return
    pytest.skip("Set AFKBOT_LIVE_ENABLE=1 to run live integration matrix tests.")
