"""PartyFlow webhook runtime tests."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channel_routing import ChannelBindingRule, ChannelBindingService
from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.endpoint_contracts import (
    ChannelIngressBatchConfig,
    PartyFlowWebhookEndpointConfig,
)
from afkbot.services.channels.endpoint_service import (
    get_channel_endpoint_service,
    reset_channel_endpoint_services_async,
)
from afkbot.services.channels.ingress_journal import reset_channel_ingress_journal_services_async
from afkbot.services.channels.ingress_persistence import (
    get_channel_ingress_pending_service,
    reset_channel_ingress_pending_services_async,
)
from afkbot.services.channels.partyflow_runtime_registry import (
    reset_partyflow_webhook_runtime_registries,
)
from afkbot.services.channels.partyflow_webhook import PartyFlowWebhookService
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService, reset_profile_services_async
from afkbot.settings import Settings


@pytest.fixture(autouse=True)
async def _reset_cached_services() -> None:
    """Dispose cached services and runtime registries around each PartyFlow test."""

    await reset_channel_binding_services_async()
    await reset_channel_endpoint_services_async()
    await reset_channel_ingress_journal_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_profile_services_async()
    reset_partyflow_webhook_runtime_registries()
    yield
    await reset_channel_binding_services_async()
    await reset_channel_endpoint_services_async()
    await reset_channel_ingress_journal_services_async()
    await reset_channel_ingress_pending_services_async()
    await reset_profile_services_async()
    reset_partyflow_webhook_runtime_registries()


class _FakeDeliveryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def deliver_text(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        target: ChannelDeliveryTarget,
        text: str,
        credential_profile_key: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "run_id": run_id,
                "target": target,
                "text": text,
                "credential_profile_key": credential_profile_key,
            }
        )
        return {"ok": True}


async def _seed_profile_and_binding(settings: Settings) -> None:
    engine = create_engine(settings)
    await create_schema(engine)
    await engine.dispose()
    profiles = ProfileService(settings)
    bindings = ChannelBindingService(settings)
    endpoints = get_channel_endpoint_service(settings)
    try:
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
            policy_network_allowlist=("api.partyflow.ru",),
        )
        await endpoints.create(_endpoint())
        await bindings.put(
            ChannelBindingRule(
                binding_id="partyflow-main",
                transport="partyflow",
                account_id="partyflow-bot",
                profile_id="default",
                session_policy="per-thread",
                prompt_overlay="Reply as PartyFlow bot.",
            )
        )
    finally:
        await endpoints.shutdown()
        await bindings.shutdown()
        await profiles.shutdown()


def _endpoint(
    *,
    trigger_mode: str = "mention",
    ingress_batch: ChannelIngressBatchConfig | None = None,
    reply_mode: str = "same_conversation",
    trigger_keywords: tuple[str, ...] = (),
) -> PartyFlowWebhookEndpointConfig:
    return PartyFlowWebhookEndpointConfig(
        endpoint_id="partyflow-main",
        profile_id="default",
        credential_profile_key="partyflow-main",
        account_id="partyflow-bot",
        trigger_mode=trigger_mode,
        trigger_keywords=trigger_keywords,
        ingress_batch=ingress_batch or ChannelIngressBatchConfig(),
        reply_mode=reply_mode,
    )


def _build_headers(
    *,
    secret: bytes,
    body: bytes,
    delivery_id: str,
    timestamp: int | None = None,
) -> dict[str, str]:
    timestamp_value = str(int(time.time()) if timestamp is None else int(timestamp))
    signature = (
        "sha256="
        + hmac.new(
            secret,
            f"v1:{timestamp_value}:".encode("utf-8") + body,
            hashlib.sha256,
        ).hexdigest()
    )
    return {
        "x-partyflow-delivery-id": delivery_id,
        "x-partyflow-timestamp": timestamp_value,
        "x-partyflow-signature": signature,
    }


async def test_partyflow_webhook_batches_messages_and_delivers_reply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook runtime should batch sequential messages into one turn and reply in the same thread."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_webhook.db'}",
    )
    await _seed_profile_and_binding(settings)
    delivery = _FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=301,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="partyflow reply"),
        )

    service = PartyFlowWebhookService(
        settings,
        endpoint=_endpoint(
            ingress_batch=ChannelIngressBatchConfig(
                enabled=True,
                debounce_ms=100,
                max_batch_size=10,
                max_buffer_chars=4000,
            )
        ),
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
    )

    async def fake_bootstrap() -> None:
        service._bot_id = "bot-42"  # type: ignore[attr-defined]
        service._signing_secret = b"signing-secret"  # type: ignore[attr-defined]

    monkeypatch.setattr(service, "_bootstrap_identity", fake_bootstrap)

    payload_one = {
        "event_type": "MESSAGE_CREATED",
        "event_id": "evt-1",
        "occurred_at": "2026-04-20T10:00:00+00:00",
        "data": {
            "id": "msg-1",
            "conversation_id": "conv-1",
            "thread": {"id": "thread-9"},
            "author": {"id": "user-1"},
            "content": "hello first",
            "mentions": [{"user_id": "bot-42"}],
            "conversation_type": "group",
            "created_at": "2026-04-20T10:00:00+00:00",
        },
    }
    payload_two = {
        "event_type": "MESSAGE_CREATED",
        "event_id": "evt-2",
        "occurred_at": "2026-04-20T10:00:01+00:00",
        "context": {
            "messages": [
                {
                    "id": "ctx-1",
                    "author": {"id": "user-1"},
                    "text": "earlier context",
                    "created_at": "2026-04-20T09:59:59+00:00",
                }
            ]
        },
        "data": {
            "message_id": "msg-2",
            "conversation_id": "conv-1",
            "thread_id": "thread-9",
            "author_id": "user-1",
            "text": "hello second",
            "mentioned_users": [{"id": "bot-42"}],
            "conversation_type": "group",
        },
    }

    await service.start()
    try:
        first_body = json.dumps(payload_one, separators=(",", ":")).encode("utf-8")
        first_status, _ = await service.handle_webhook(
            headers=_build_headers(
                secret=b"signing-secret", body=first_body, delivery_id="delivery-1"
            ),
            body=first_body,
        )
        second_body = json.dumps(payload_two, separators=(",", ":")).encode("utf-8")
        second_status, _ = await service.handle_webhook(
            headers=_build_headers(
                secret=b"signing-secret", body=second_body, delivery_id="delivery-2"
            ),
            body=second_body,
        )
        await asyncio.sleep(0.25)
    finally:
        await service.stop()

    assert first_status == 202
    assert second_status == 202
    assert len(captured) == 1
    assert "Multiple recent messages arrived from the same conversation." in str(
        captured[0]["message"]
    )
    assert "hello first" in str(captured[0]["message"])
    assert "hello second" in str(captured[0]["message"])
    assert "Recent PartyFlow context before this message:" in str(captured[0]["message"])
    assert (
        captured[0]["client_msg_id"]
        == "partyflow-batch:partyflow-bot:conv-1:thread-9:user-1:msg-1:msg-2:2"
    )
    context_overrides = captured[0]["context_overrides"]
    assert context_overrides.runtime_metadata["channel_ingress_batch"]["message_count"] == 2
    assert len(delivery.calls) == 1
    assert delivery.calls[0]["text"] == "partyflow reply"
    target = delivery.calls[0]["target"]
    assert isinstance(target, ChannelDeliveryTarget)
    assert target.transport == "partyflow"
    assert target.peer_id == "conv-1"
    assert target.thread_id == "thread-9"
    pending = await get_channel_ingress_pending_service(settings).list_pending(
        endpoint_id="partyflow-main"
    )
    assert pending == []


async def test_partyflow_webhook_ignores_self_authored_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook runtime should ignore bot-authored messages to avoid reply loops."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_webhook_self.db'}",
    )
    await _seed_profile_and_binding(settings)
    service = PartyFlowWebhookService(
        settings,
        endpoint=_endpoint(reply_mode="disabled"),
    )

    async def fake_bootstrap() -> None:
        service._bot_id = "bot-42"  # type: ignore[attr-defined]
        service._signing_secret = b"signing-secret"  # type: ignore[attr-defined]

    monkeypatch.setattr(service, "_bootstrap_identity", fake_bootstrap)
    payload = {
        "event_type": "MESSAGE_CREATED",
        "event_id": "evt-1",
        "conversation_id": "conv-1",
        "data": {
            "message_id": "msg-1",
            "author_id": "bot-42",
            "text": "@bot should be ignored",
            "mentions": ["bot-42"],
        },
    }

    await service.start()
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        status, response = await service.handle_webhook(
            headers=_build_headers(
                secret=b"signing-secret", body=body, delivery_id="delivery-self"
            ),
            body=body,
        )
        await asyncio.sleep(0.05)
    finally:
        await service.stop()

    assert status == 200
    assert response == {"ok": True, "ignored": True}
    pending = await get_channel_ingress_pending_service(settings).list_pending(
        endpoint_id="partyflow-main"
    )
    assert pending == []


async def test_partyflow_webhook_rejects_old_replay_timestamps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Webhook runtime should reject stale signed requests older than the allowed window."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_webhook_replay.db'}",
    )
    await _seed_profile_and_binding(settings)
    service = PartyFlowWebhookService(
        settings,
        endpoint=_endpoint(reply_mode="disabled"),
    )

    async def fake_bootstrap() -> None:
        service._bot_id = "bot-42"  # type: ignore[attr-defined]
        service._signing_secret = b"signing-secret"  # type: ignore[attr-defined]

    monkeypatch.setattr(service, "_bootstrap_identity", fake_bootstrap)
    payload = {
        "event_type": "MESSAGE_CREATED",
        "event_id": "evt-1",
        "conversation_id": "conv-1",
        "data": {
            "message_id": "msg-1",
            "author_id": "user-1",
            "text": "@bot stale replay",
            "mentions": ["bot-42"],
        },
    }

    await service.start()
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        status, response = await service.handle_webhook(
            headers=_build_headers(
                secret=b"signing-secret",
                body=body,
                delivery_id="delivery-stale",
                timestamp=int(time.time()) - 301,
            ),
            body=body,
        )
    finally:
        await service.stop()

    assert status == 401
    assert response == {
        "ok": False,
        "error_code": "partyflow_invalid_signature",
        "reason": "Invalid PartyFlow webhook signature",
    }


async def test_partyflow_webhook_keyword_trigger_uses_token_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyword mode should not trigger on substring matches like `fail` inside `failover`."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'partyflow_webhook_keywords.db'}",
    )
    await _seed_profile_and_binding(settings)
    service = PartyFlowWebhookService(
        settings,
        endpoint=_endpoint(
            trigger_mode="keywords",
            trigger_keywords=("fail",),
            reply_mode="disabled",
        ),
    )

    async def fake_bootstrap() -> None:
        service._bot_id = "bot-42"  # type: ignore[attr-defined]
        service._signing_secret = b"signing-secret"  # type: ignore[attr-defined]

    monkeypatch.setattr(service, "_bootstrap_identity", fake_bootstrap)
    payload = {
        "event_type": "MESSAGE_CREATED",
        "event_id": "evt-1",
        "conversation_id": "conv-1",
        "data": {
            "message_id": "msg-1",
            "author_id": "user-1",
            "text": "failover is starting now",
        },
    }

    await service.start()
    try:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        status, response = await service.handle_webhook(
            headers=_build_headers(
                secret=b"signing-secret",
                body=body,
                delivery_id="delivery-keyword-boundary",
            ),
            body=body,
        )
        await asyncio.sleep(0.05)
    finally:
        await service.stop()

    assert status == 200
    assert response == {"ok": True, "ignored": True}
