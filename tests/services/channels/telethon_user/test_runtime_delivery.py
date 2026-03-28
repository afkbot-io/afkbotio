"""Delivery-focused tests for the Telethon user-channel runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channels.endpoint_contracts import (
    ChannelIngressBatchConfig,
    ChannelReplyHumanizationConfig,
)
from afkbot.services.channels.telethon_user.service import TelethonUserService
from afkbot.settings import Settings
from tests.services.channels.telethon_user._harness import (
    FakeDeliveryService,
    FakeTelethonClient,
    endpoint,
    fake_resolve_credentials,
    seed_profile_and_binding,
)


async def test_telethon_user_service_suppresses_llm_error_replies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient LLM failures should not be echoed back into Telegram chats."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_llm_error.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()
    delivery = FakeDeliveryService()

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        _ = kwargs
        return TurnResult(
            run_id=72,
            profile_id="default",
            session_id="telegram_user:42",
            envelope=ActionEnvelope(
                action="finalize",
                message="LLM provider is temporarily unavailable. Please try again shortly.",
                spec_patch={"error_code": "llm_provider_network_error"},
            ),
        )

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_telethon_credentials",
        fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.import_telethon",
        lambda: SimpleNamespace(events_module=SimpleNamespace(NewMessage=lambda **kwargs: kwargs)),
    )

    service = TelethonUserService(
        settings,
        endpoint=endpoint(),
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=6),
                raw_text="hello there",
                out=False,
                is_private=True,
                is_group=False,
                chat_id=42,
                sender_id=777,
                is_reply=False,
            )
        )
        await asyncio.wait_for(service._queue.join(), timeout=2.0)  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert delivery.calls == []


async def test_telethon_user_service_marks_read_and_typing_before_reply_when_humanization_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telethon replies should acknowledge read state and show typing before sending when enabled."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_humanize.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()
    delivery = FakeDeliveryService()

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        return TurnResult(
            run_id=71,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="hello from userbot"),
        )

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_telethon_credentials",
        fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.import_telethon",
        lambda: SimpleNamespace(events_module=SimpleNamespace(NewMessage=lambda **kwargs: kwargs)),
    )

    service = TelethonUserService(
        settings,
        endpoint=endpoint(
            reply_humanization=ChannelReplyHumanizationConfig(
                enabled=True,
                min_delay_ms=0,
                max_delay_ms=1,
                chars_per_second=120,
            ),
            mark_read_before_reply=True,
        ),
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        assert fake_client.handler is not None
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=5),
                raw_text="hello there",
                out=False,
                is_private=True,
                is_group=False,
                chat_id=42,
                sender_id=777,
                is_reply=False,
            )
        )
        await asyncio.wait_for(service._queue.join(), timeout=1.0)  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert fake_client.read_ack_calls == [{"entity": 42, "max_id": 5}]
    assert fake_client.typing_calls == [{"entity": 42, "action": "typing"}]
    assert len(delivery.calls) == 1


async def test_telethon_user_service_batches_sequential_messages_when_ingress_batch_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telethon adapter should merge sequential messages into one turn when ingress batching is enabled."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_batch.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=73,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="hello from batched userbot"),
        )

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_telethon_credentials",
        fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.import_telethon",
        lambda: SimpleNamespace(events_module=SimpleNamespace(NewMessage=lambda **kwargs: kwargs)),
    )

    service = TelethonUserService(
        settings,
        endpoint=endpoint(
            ingress_batch=ChannelIngressBatchConfig(
                enabled=True,
                debounce_ms=100,
                max_batch_size=10,
                max_buffer_chars=4000,
            )
        ),
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=5),
                raw_text="first",
                out=False,
                is_private=True,
                is_group=False,
                chat_id=42,
                sender_id=777,
                is_reply=False,
            )
        )
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=6),
                raw_text="second",
                out=False,
                is_private=True,
                is_group=False,
                chat_id=42,
                sender_id=777,
                is_reply=False,
            )
        )
        await asyncio.wait_for(service._queue.join(), timeout=1.0)  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert len(captured) == 1
    assert "Multiple recent messages arrived from the same conversation." in str(captured[0]["message"])
    assert "first" in str(captured[0]["message"])
    assert "second" in str(captured[0]["message"])
    assert captured[0]["client_msg_id"] == "telethon-batch:tg-user:42:-:777:5:6:2"
    context_overrides = captured[0]["context_overrides"]
    assert context_overrides.runtime_metadata["channel_ingress_batch"]["message_count"] == 2
    assert len(delivery.calls) == 1
