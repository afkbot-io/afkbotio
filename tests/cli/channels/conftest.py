
"""Pytest fixtures for channel CLI test modules."""

import asyncio

import pytest

from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channels.endpoint_service import reset_channel_endpoint_services_async
from afkbot.services.profile_runtime.service import reset_profile_services_async
from tests.cli.channels._harness import _reset_owned_profile_services_async


@pytest.fixture(autouse=True)
def _reset_cached_services() -> None:
    """Dispose cached async services before and after each CLI channel test."""

    asyncio.run(_reset_owned_profile_services_async())
    asyncio.run(reset_channel_endpoint_services_async())
    asyncio.run(reset_channel_binding_services_async())
    asyncio.run(reset_profile_services_async())
    yield
    asyncio.run(_reset_owned_profile_services_async())
    asyncio.run(reset_channel_endpoint_services_async())
    asyncio.run(reset_channel_binding_services_async())
    asyncio.run(reset_profile_services_async())
