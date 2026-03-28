"""Reactive reply and batching tests for the Telethon user-channel runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channels.telethon_user.service import TelethonUserService
from afkbot.settings import Settings
from tests.services.channels.telethon_user._harness import (
    FakeDeliveryService,
    FakeTelethonClient,
    endpoint,
    fake_resolve_credentials,
    seed_profile_and_binding,
)


async def test_telethon_user_service_processes_private_message_and_replies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telethon adapter should route private text messages through bindings and reply."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
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
        endpoint=endpoint(),
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
        await asyncio.wait_for(service._queue.join(), timeout=2.0)  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert len(captured) == 1
    assert captured[0]["message"] == "hello there"
    assert captured[0]["profile_id"] == "default"
    assert captured[0]["client_msg_id"] == "telethon:tg-user:42:5"
    assert len(delivery.calls) == 1
    assert delivery.calls[0]["text"] == "hello from userbot"
    assert delivery.calls[0]["credential_profile_key"] == "tg-user-main"
    assert delivery.calls[0]["target"].model_dump(exclude_none=True) == {
        "transport": "telegram_user",
        "account_id": "tg-user",
        "peer_id": "42",
        "user_id": "777",
    }


async def test_telethon_user_service_filters_reactive_replies_by_chat_name_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reactive Telethon replies should honor name-based allowlist/blocklist filters."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=72,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="filtered reply"),
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
            reply_allowed_chat_patterns=("андрей",),
            reply_blocked_chat_patterns=("спам",),
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
                message=SimpleNamespace(id=6),
                raw_text="hello from arkady",
                out=False,
                is_private=True,
                is_group=False,
                chat_id=43,
                sender_id=778,
                sender=SimpleNamespace(first_name="Аркадий", last_name="Нестеров", username="arkady"),
                chat=SimpleNamespace(first_name="Аркадий", last_name="Нестеров", username="arkady"),
                is_reply=False,
            )
        )
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=7),
                raw_text="hello from andrey",
                out=False,
                is_private=True,
                is_group=False,
                chat_id=44,
                sender_id=779,
                sender=SimpleNamespace(first_name="Андрей", last_name="Нестеров", username="andrey"),
                chat=SimpleNamespace(first_name="Андрей", last_name="Нестеров", username="andrey"),
                is_reply=False,
            )
        )
        await asyncio.wait_for(service._queue.join(), timeout=1.0)  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert len(captured) == 1
    assert captured[0]["message"] == "hello from andrey"
    assert len(delivery.calls) == 1
    assert delivery.calls[0]["target"].model_dump(exclude_none=True) == {
        "transport": "telegram_user",
        "account_id": "tg-user",
        "peer_id": "44",
        "user_id": "779",
    }


async def test_telethon_user_service_can_resolve_reply_filter_name_via_event_entity_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reactive reply filters should use Telethon entity fetch helpers when inline title is absent."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user.db'}",
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
            envelope=ActionEnvelope(action="finalize", message="fetched title reply"),
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
        endpoint=endpoint(reply_allowed_chat_patterns=("андрей",)),
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    async def get_sender() -> object:
        return SimpleNamespace(first_name="Андрей", last_name="К.", username="andrey")

    await service.start()
    try:
        assert fake_client.handler is not None
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=8),
                raw_text="hello after fetch",
                out=False,
                is_private=True,
                is_group=False,
                chat_id=45,
                sender_id=780,
                chat=None,
                sender=None,
                get_sender=get_sender,
                is_reply=False,
            )
        )
        await asyncio.wait_for(service._queue.join(), timeout=1.0)  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert len(captured) == 1
    assert captured[0]["message"] == "hello after fetch"
    assert len(delivery.calls) == 1


async def test_telethon_user_service_reply_filters_match_private_username_without_spending_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reactive filters should match private usernames even when display names are present."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_username.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=74,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="username matched"),
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
        endpoint=endpoint(reply_allowed_chat_patterns=("@andrey",)),
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        assert fake_client.handler is not None
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=9),
                raw_text="hello from username filter",
                out=False,
                is_private=True,
                is_group=False,
                chat_id=46,
                sender_id=781,
                sender=SimpleNamespace(first_name="Андрей", last_name="Нестеров", username="andrey"),
                chat=SimpleNamespace(first_name="Андрей", last_name="Нестеров", username="andrey"),
                is_reply=False,
            )
        )
        await asyncio.wait_for(service._queue.join(), timeout=1.0)  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert len(captured) == 1
    assert captured[0]["message"] == "hello from username filter"
    assert len(delivery.calls) == 1


async def test_telethon_user_service_group_filter_does_not_fall_back_to_sender_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Group reply filters should match group/chat identity, not one sender fallback."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_group_filter.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=75,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="unexpected group match"),
        )

    async def get_sender() -> object:
        return SimpleNamespace(first_name="Андрей", last_name="К.", username="andrey")

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
            reply_allowed_chat_patterns=("андрей",),
            group_invocation_mode="all_messages",
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
                message=SimpleNamespace(id=10),
                raw_text="group hello",
                out=False,
                is_private=False,
                is_group=True,
                chat_id=-100123,
                sender_id=782,
                chat=None,
                sender=None,
                get_sender=get_sender,
                is_reply=False,
            )
        )
        await asyncio.wait_for(service._queue.join(), timeout=1.0)  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert captured == []
    assert delivery.calls == []
