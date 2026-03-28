"""Live integration matrix tests (env-gated)."""

from __future__ import annotations

import os

import pytest

from afkbot.services.health import run_integration_matrix
from afkbot.settings import get_settings


@pytest.mark.live
async def test_live_integration_matrix_probe() -> None:
    """Run live integration probe matrix against current runtime profile."""

    settings = get_settings()
    profile_id = os.getenv("AFKBOT_LIVE_PROFILE_ID", "default").strip() or "default"
    credential_profile_key = (
        os.getenv("AFKBOT_LIVE_CREDENTIAL_PROFILE", "default").strip()
        or "default"
    )
    report = await run_integration_matrix(
        settings,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
        probe=True,
    )
    assert report.ok, report
