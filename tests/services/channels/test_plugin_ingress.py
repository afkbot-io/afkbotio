"""Tests for plugin channel ingress dispatch helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channel_routing.contracts import ChannelBindingRule
from afkbot.services.channel_routing.service import (
    ChannelBindingServiceError,
    get_channel_binding_service,
)
from afkbot.services.channels.endpoint_contracts import ChannelAccessPolicy, ChannelEndpointConfig
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.channels.plugin_ingress import (
    PluginChannelIngressDispatcher,
    PluginInboundMessage,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService
from afkbot.settings import Settings


@pytest.mark.asyncio
async def test_plugin_channel_ingress_dispatches_turn_and_reply(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'plugin_ingress.db'}",
    )
    endpoint = ChannelEndpointConfig(
        endpoint_id="avito-main",
        transport="avito",
        adapter_kind="avito_polling",
        profile_id="default",
        credential_profile_key="avito-main",
        account_id="seller-1",
        access_policy=ChannelAccessPolicy(private_policy="open"),
    )
    profiles = ProfileService(settings)
    endpoints = get_channel_endpoint_service(settings)
    await profiles.create(
        profile_id="default",
        name="Default",
        runtime_config=ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        ),
        runtime_secrets=None,
        policy_enabled=True,
        policy_preset="medium",
        policy_capabilities=("files",),
        policy_network_allowlist=("*",),
    )
    await endpoints.create(endpoint)
    bindings = get_channel_binding_service(settings)
    await bindings.put(
        ChannelBindingRule(
            binding_id="avito-buyer-1",
            transport="avito",
            profile_id="default",
            account_id="seller-1",
            peer_id="buyer-1",
            session_policy="per-chat",
        )
    )
    run_calls: list[dict[str, object]] = []
    delivery_calls: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        run_calls.append(dict(kwargs))
        return TurnResult(
            run_id=7,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="reply from agent"),
        )

    class FakeDeliveryService:
        async def deliver_text(self, **kwargs: object) -> object:
            delivery_calls.append(dict(kwargs))
            return object()

    dispatcher = PluginChannelIngressDispatcher(
        settings,
        endpoint=endpoint,
        channel_delivery_service=FakeDeliveryService(),  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
    )

    first = await dispatcher.dispatch_text(
        PluginInboundMessage(
            peer_id="buyer-1",
            user_id="user-1",
            text="hello",
            event_key="event-1",
        )
    )
    second = await dispatcher.dispatch_text(
        PluginInboundMessage(
            peer_id="buyer-1",
            user_id="user-1",
            text="hello again",
            event_key="event-1",
        )
    )

    assert first is not None
    assert second is None
    assert run_calls[0]["message"] == "hello"
    assert run_calls[0]["profile_id"] == "default"
    assert run_calls[0]["session_id"] == (
        "profile:default:channel:avito:account:seller-1:chat:buyer-1"
    )
    context_overrides = run_calls[0]["context_overrides"]
    assert "channel.send" in context_overrides.channel_owned_tool_names
    assert context_overrides.trusted_runtime_context["active_channel"]["transport"] == "avito"
    assert delivery_calls[0]["credential_profile_key"] == "avito-main"
    target = delivery_calls[0]["target"]
    assert target.transport == "avito"
    assert target.adapter_kind == "avito_polling"
    assert target.peer_id == "buyer-1"
    await profiles.shutdown()
    await endpoints.shutdown()
    await bindings.shutdown()


@pytest.mark.asyncio
async def test_plugin_channel_ingress_rejects_empty_routing_keys(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'plugin_ingress_invalid.db'}",
    )
    endpoint = ChannelEndpointConfig(
        endpoint_id="avito-main",
        transport="avito",
        adapter_kind="avito_polling",
        profile_id="default",
        credential_profile_key="avito-main",
        account_id="seller-1",
        access_policy=ChannelAccessPolicy(private_policy="open"),
    )
    dispatcher = PluginChannelIngressDispatcher(settings, endpoint=endpoint)

    with pytest.raises(ValueError, match="peer_id"):
        await dispatcher.dispatch_text(
            PluginInboundMessage(peer_id=" ", text="hello", event_key="event-1")
        )
    with pytest.raises(ValueError, match="event_key"):
        await dispatcher.dispatch_text(
            PluginInboundMessage(peer_id="buyer-1", text="hello", event_key=" ")
        )


@pytest.mark.asyncio
async def test_plugin_channel_ingress_fails_closed_when_binding_is_required(tmp_path: Path) -> None:
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'plugin_ingress_strict.db'}",
    )
    endpoint = ChannelEndpointConfig(
        endpoint_id="avito-main",
        transport="avito",
        adapter_kind="avito_polling",
        profile_id="default",
        credential_profile_key="avito-main",
        account_id="seller-1",
        access_policy=ChannelAccessPolicy(private_policy="open"),
    )
    profiles = ProfileService(settings)
    endpoints = get_channel_endpoint_service(settings)
    await profiles.create(
        profile_id="default",
        name="Default",
        runtime_config=ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        ),
        runtime_secrets=None,
        policy_enabled=True,
        policy_preset="medium",
        policy_capabilities=("files",),
        policy_network_allowlist=("*",),
    )
    await endpoints.create(endpoint)
    dispatcher = PluginChannelIngressDispatcher(settings, endpoint=endpoint)

    with pytest.raises(ChannelBindingServiceError) as exc_info:
        await dispatcher.dispatch_text(
            PluginInboundMessage(
                peer_id="buyer-1",
                user_id="user-1",
                text="hello",
                event_key="event-1",
            ),
            require_binding_match=True,
        )

    assert exc_info.value.error_code == "channel_binding_no_match"
    await profiles.shutdown()
    await endpoints.shutdown()
