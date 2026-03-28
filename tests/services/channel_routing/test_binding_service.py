"""Tests for persisted channel binding service."""

from __future__ import annotations

import pytest

from afkbot.services.channel_routing import (
    ChannelBindingRule,
    ChannelBindingService,
    ChannelBindingServiceError,
    ChannelRoutingInput,
    get_channel_binding_service,
    reset_channel_binding_services_async,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService
from afkbot.settings import Settings


async def _create_profiles(settings: Settings) -> None:
    service = ProfileService(settings)
    try:
        for profile_id, name in (("default", "Default"), ("sales", "Sales")):
            await service.create(
                profile_id=profile_id,
                name=name,
                runtime_config=ProfileRuntimeConfig(
                    llm_provider="openai",
                    llm_model="gpt-4o-mini",
                ),
                runtime_secrets=None,
                policy_enabled=True,
                policy_preset="medium",
                policy_capabilities=("files",),
                policy_network_allowlist=(),
            )
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_channel_binding_service_persists_and_resolves_rules(tmp_path) -> None:
    """Service should persist bindings and resolve routing via stored rules."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}", root_dir=tmp_path)
    await _create_profiles(settings)
    service = ChannelBindingService(settings)
    try:
        await service.put(
            ChannelBindingRule(
                binding_id="telegram-default",
                transport="telegram",
                profile_id="default",
                session_policy="main",
            )
        )
        peer_rule = await service.put(
            ChannelBindingRule(
                binding_id="telegram-peer-42",
                transport="telegram",
                profile_id="sales",
                session_policy="per-thread",
                peer_id="42",
            )
        )

        listed = await service.list(transport="telegram")
        loaded = await service.get(binding_id="telegram-peer-42")
        decision = await service.resolve(
            routing_input=ChannelRoutingInput(
                transport="telegram",
                peer_id="42",
                thread_id="9001",
                default_session_id="main",
            )
        )

        assert [item.binding_id for item in listed] == ["telegram-default", "telegram-peer-42"]
        assert loaded == peer_rule
        assert decision is not None
        assert decision.binding_id == "telegram-peer-42"
        assert decision.profile_id == "sales"
        assert decision.session_id == "profile:sales:chat:42:thread:9001"
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_channel_binding_service_rejects_unknown_profile(tmp_path) -> None:
    """Service should refuse bindings targeting missing profiles."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}", root_dir=tmp_path)
    service = ChannelBindingService(settings)
    try:
        with pytest.raises(ChannelBindingServiceError, match="Profile not found"):
            await service.put(
                ChannelBindingRule(
                    binding_id="api-default",
                    transport="api",
                    profile_id="missing",
                    session_policy="main",
                )
            )
    finally:
        await service.shutdown()


@pytest.mark.asyncio
async def test_reset_channel_binding_services_async_disposes_cached_services(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Async reset should call shutdown before clearing cached routing services."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}", root_dir=tmp_path)
    service = get_channel_binding_service(settings)
    disposed = False

    async def _fake_shutdown() -> None:
        nonlocal disposed
        disposed = True

    monkeypatch.setattr(service, "shutdown", _fake_shutdown)

    await reset_channel_binding_services_async()

    assert disposed is True
    assert get_channel_binding_service(settings) is not service


def test_get_channel_binding_service_returns_fresh_service_outside_running_loop(tmp_path) -> None:
    """Sync CLI call-sites should not reuse one async binding service across loops."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}", root_dir=tmp_path)

    first = get_channel_binding_service(settings)
    second = get_channel_binding_service(settings)

    assert first is not second


@pytest.mark.asyncio
async def test_channel_binding_service_normalizes_transport_case(tmp_path) -> None:
    """Mixed-case transport values should persist and resolve as lowercase."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}", root_dir=tmp_path)
    await _create_profiles(settings)
    service = ChannelBindingService(settings)
    try:
        await service.put(
            ChannelBindingRule(
                binding_id="telegram-case",
                transport="Telegram",
                profile_id="default",
                session_policy="main",
            )
        )
        listed = await service.list(transport="telegram")
        decision = await service.resolve(
            routing_input=ChannelRoutingInput(
                transport="telegram",
                default_session_id="main",
            )
        )

        assert [item.transport for item in listed] == ["telegram"]
        assert decision is not None
        assert decision.binding_id == "telegram-case"
    finally:
        await service.shutdown()
