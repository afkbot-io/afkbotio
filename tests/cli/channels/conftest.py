"""Pytest fixtures for channel CLI test modules."""

import asyncio

import pytest

from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channels.endpoint_service import reset_channel_endpoint_services_async
from afkbot.services.profile_runtime.service import reset_profile_services_async
from tests.cli.channels._harness import _reset_owned_profile_services_async


async def _reset_channel_cli_services_async() -> None:
    """Dispose channel CLI service caches on one loop before closing it."""

    await _reset_owned_profile_services_async()
    await reset_channel_endpoint_services_async()
    await reset_channel_binding_services_async()
    await reset_profile_services_async()
    await asyncio.sleep(0.05)


@pytest.fixture(autouse=True)
def _reset_cached_services() -> None:
    """Dispose cached async services before and after each CLI channel test."""

    asyncio.run(_reset_channel_cli_services_async())
    yield
    asyncio.run(_reset_channel_cli_services_async())
