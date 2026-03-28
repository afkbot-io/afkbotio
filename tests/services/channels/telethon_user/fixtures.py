"""Shared pytest fixtures for Telethon user-channel runtime tests."""

from __future__ import annotations

import pytest

from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channels.endpoint_service import reset_channel_endpoint_services_async
from afkbot.services.channels.ingress_journal import reset_channel_ingress_journal_services_async
from afkbot.services.channels.ingress_persistence import reset_channel_ingress_pending_services_async
from afkbot.services.channels.runtime_lease_registry import reset_channel_runtime_lease_registries
from afkbot.services.channels.sender_registry import reset_channel_sender_registries
from afkbot.services.profile_runtime.service import reset_profile_services_async


@pytest.fixture(autouse=True)
async def reset_cached_services() -> None:
    """Dispose cached profile, binding, ingress, and sender services around each test."""

    await reset_channel_endpoint_services_async()
    await reset_channel_binding_services_async()
    await reset_channel_ingress_journal_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_profile_services_async()
    reset_channel_sender_registries()
    reset_channel_runtime_lease_registries()
    yield
    await reset_channel_endpoint_services_async()
    await reset_channel_binding_services_async()
    await reset_channel_ingress_journal_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_profile_services_async()
    reset_channel_sender_registries()
    reset_channel_runtime_lease_registries()
