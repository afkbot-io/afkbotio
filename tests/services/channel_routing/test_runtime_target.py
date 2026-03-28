"""Tests for runtime target resolution policy over channel bindings."""

from __future__ import annotations

import asyncio

import pytest

from afkbot.services.channel_routing.contracts import ChannelBindingRule
from afkbot.services.channel_routing.service import ChannelBindingService
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channel_routing.service import get_channel_binding_service
from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channel_routing.runtime_target import build_routing_context_overrides
from afkbot.services.channel_routing.runtime_target import resolve_runtime_target
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService, reset_profile_services_async
from afkbot.settings import Settings


@pytest.fixture(autouse=True)
def _cleanup_cached_services() -> None:
    """Dispose cached async services between tests to avoid loop-close warnings."""

    yield
    asyncio.run(reset_channel_binding_services_async())
    asyncio.run(reset_profile_services_async())


async def test_runtime_target_allows_internal_transport_fallback_without_binding_match(
    tmp_path,
) -> None:
    """Internal transports should preserve fallback behavior when no binding matches."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime_target.db'}")

    target = await resolve_runtime_target(
        settings=settings,
        explicit_profile_id="default",
        explicit_session_id="api-session",
        resolve_binding=True,
        transport="api",
        default_profile_id="default",
        default_session_id="api-session",
    )

    assert target.profile_id == "default"
    assert target.session_id == "api-session"
    assert target.routing is None

    diagnostics = await get_channel_binding_service(settings).diagnostics()
    assert diagnostics.total == 1
    assert diagnostics.fallback_used == 1
    assert diagnostics.strict_no_match == 0


async def test_runtime_target_requires_binding_match_for_external_transport_by_default(
    tmp_path,
) -> None:
    """External transports should fail closed when binding resolution finds no match."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime_target.db'}")

    with pytest.raises(ChannelBindingServiceError) as exc_info:
        await resolve_runtime_target(
            settings=settings,
            explicit_profile_id="default",
            explicit_session_id="api-session",
            resolve_binding=True,
            transport="telegram",
            default_profile_id="default",
            default_session_id="api-session",
        )

    assert exc_info.value.error_code == "channel_binding_no_match"

    diagnostics = await get_channel_binding_service(settings).diagnostics()
    assert diagnostics.total == 1
    assert diagnostics.no_match == 1
    assert diagnostics.strict_no_match == 1


async def test_runtime_target_normalizes_transport_case_for_binding_match(tmp_path) -> None:
    """Mixed-case transport selectors should still match lowercase persisted bindings."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime_target.db'}")
    profiles = ProfileService(settings)
    bindings = ChannelBindingService(settings)
    try:
        await profiles.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(llm_provider="openai", llm_model="gpt-4o-mini"),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=(),
        )
        await bindings.put(
            ChannelBindingRule(
                binding_id="telegram-peer-42",
                transport="telegram",
                profile_id="default",
                session_policy="per-thread",
                peer_id="42",
            )
        )

        target = await resolve_runtime_target(
            settings=settings,
            explicit_profile_id="default",
            explicit_session_id="fallback-session",
            resolve_binding=True,
            transport="Telegram",
            peer_id="42",
            thread_id="9001",
            default_profile_id="default",
            default_session_id="fallback-session",
        )

        assert target.profile_id == "default"
        assert target.session_id == "profile:default:chat:42:thread:9001"
        assert target.routing is not None

        diagnostics = await get_channel_binding_service(settings).diagnostics()
        assert diagnostics.total == 1
        assert diagnostics.matched == 1
        assert diagnostics.recent_events[-1].binding_id == "telegram-peer-42"
    finally:
        await bindings.shutdown()
        await profiles.shutdown()


async def test_runtime_target_uses_settings_fallback_policy(tmp_path) -> None:
    """Settings should control which transports may fall back without bindings."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime_target.db'}",
        channel_routing_fallback_transports=("cli", "api", "telegram"),
    )

    target = await resolve_runtime_target(
        settings=settings,
        explicit_profile_id="default",
        explicit_session_id="fallback-session",
        resolve_binding=True,
        transport="telegram",
        default_profile_id="default",
        default_session_id="fallback-session",
    )

    assert target.profile_id == "default"
    assert target.session_id == "fallback-session"


async def test_routing_context_overrides_add_public_channel_overlay_for_external_transport(tmp_path) -> None:
    """External transports should receive trusted user-facing prompt guidance."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime_target.db'}")
    profiles = ProfileService(settings)
    bindings = ChannelBindingService(settings)
    try:
        await profiles.create(
            profile_id="support",
            name="Support",
            runtime_config=ProfileRuntimeConfig(llm_provider="openai", llm_model="gpt-4o-mini"),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="strict",
            policy_capabilities=("files",),
            policy_network_allowlist=(),
        )
        await bindings.put(
            ChannelBindingRule(
                binding_id="telegram-support",
                transport="telegram",
                profile_id="support",
                session_policy="per-thread",
                account_id="support-bot",
                prompt_overlay="Answer only about AFKBOT docs.",
            )
        )

        target = await resolve_runtime_target(
            settings=settings,
            explicit_profile_id=None,
            explicit_session_id=None,
            resolve_binding=True,
            transport="telegram",
            account_id="support-bot",
            peer_id="42",
            thread_id="7",
            default_profile_id="support",
            default_session_id="telegram:42",
        )
        overrides = build_routing_context_overrides(
            target=target,
            transport="telegram",
            account_id="support-bot",
            peer_id="42",
            thread_id="7",
        )

        assert overrides is not None
        assert overrides.runtime_metadata == {
            "transport": "telegram",
            "account_id": "support-bot",
            "peer_id": "42",
            "thread_id": "7",
            "channel_binding": {
                "binding_id": "telegram-support",
                "session_policy": "per-thread",
            },
        }
        assert overrides.prompt_overlay is not None
        assert "External Channel Behavior" in overrides.prompt_overlay
        assert "Answer only about AFKBOT docs." in overrides.prompt_overlay
    finally:
        await bindings.shutdown()
        await profiles.shutdown()


async def test_routing_context_overrides_skip_public_channel_overlay_for_internal_transport(tmp_path) -> None:
    """Internal transports should not receive user-facing channel guidance."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime_target.db'}")
    target = await resolve_runtime_target(
        settings=settings,
        explicit_profile_id="default",
        explicit_session_id="cli-session",
        resolve_binding=False,
        transport="cli",
        default_profile_id="default",
        default_session_id="cli-session",
    )

    overrides = build_routing_context_overrides(
        target=target,
        transport="cli",
    )

    assert overrides is not None
    assert overrides.runtime_metadata == {"transport": "cli"}
    assert overrides.prompt_overlay is None
