"""Pytest fixtures for profile CLI test modules."""

from __future__ import annotations

import asyncio

import pytest

from afkbot.services.credentials import reset_credentials_services_async
from afkbot.services.profile_runtime.service import reset_profile_services_async


async def _reset_profile_cli_services_async() -> None:
    """Dispose profile CLI service caches before pytest collects thread warnings."""

    await reset_profile_services_async()
    await reset_credentials_services_async()
    await asyncio.sleep(0.1)


@pytest.fixture(autouse=True)
def _reset_cached_services() -> None:
    """Dispose async services before and after each profile CLI test."""

    asyncio.run(_reset_profile_cli_services_async())
    yield
    asyncio.run(_reset_profile_cli_services_async())
