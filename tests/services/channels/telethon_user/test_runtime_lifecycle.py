"""Lifecycle and failure-mode tests for the Telethon user-channel runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channels.runtime_lease_registry import ChannelRuntimeLeaseRegistryError
from afkbot.services.channels.sender_registry import (
    ChannelSenderRegistryError,
    get_channel_sender_registry,
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


async def test_telethon_user_service_propagates_non_no_match_binding_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected binding failures must bubble out of one inbound event handler."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_binding_error.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_telethon_credentials",
        fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.import_telethon",
        lambda: SimpleNamespace(events_module=SimpleNamespace(NewMessage=lambda **kwargs: kwargs)),
    )

    async def broken_resolve(**kwargs: object) -> object:
        _ = kwargs
        raise ChannelBindingServiceError(
            error_code="channel_binding_profile_not_found",
            reason="Profile disappeared.",
        )

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_runtime_target",
        broken_resolve,
    )

    service = TelethonUserService(
        settings,
        endpoint=endpoint(reply_mode="disabled"),
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        with pytest.raises(ChannelBindingServiceError, match="Profile disappeared"):
            await service._handle_inbound_event(  # type: ignore[attr-defined]
                SimpleNamespace(
                    event_key="tg-user:42:5",
                    message_id=5,
                    chat_id="42",
                    chat_kind="private",
                    user_id="777",
                    thread_id=None,
                    text="hello there",
                    is_self_command=False,
                )
            )
    finally:
        await service.stop()


async def test_telethon_user_service_ignores_outgoing_non_command_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outgoing self-messages without command prefix must not trigger a turn."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_self.db'}",
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
            envelope=ActionEnvelope(action="finalize", message="ignored"),
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
        endpoint=endpoint(process_self_commands=True),
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
                raw_text="just saying hi",
                out=True,
                is_private=True,
                is_group=False,
                chat_id=42,
                sender_id=1001,
                is_reply=False,
            )
        )
        await asyncio.sleep(0)
    finally:
        await service.stop()

    assert captured == []
    assert delivery.calls == []


async def test_telethon_user_service_cleans_up_when_sender_registration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Partial Telethon startup should clean up handlers and disconnect on sender conflicts."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_conflict.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()

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
        client_factory=lambda **kwargs: fake_client,
    )
    registry = get_channel_sender_registry(settings)

    async def occupied_sender(target, text: str):  # type: ignore[no-untyped-def]
        _ = target, text
        return {"message_id": 1}

    await registry.register(transport="telegram_user", account_id="tg-user", sender=occupied_sender)
    try:
        with pytest.raises(ChannelSenderRegistryError) as exc_info:
            await service.start()
    finally:
        await registry.unregister(transport="telegram_user", account_id="tg-user", sender=occupied_sender)
        await service.stop()

    assert exc_info.value.error_code == "channel_sender_conflict"
    assert fake_client.handler is None
    assert service._client is None  # type: ignore[attr-defined]


async def test_telethon_user_service_worker_continues_after_handler_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed inbound event must not kill the Telethon worker loop."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_worker.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient()
    delivery = FakeDeliveryService()
    calls: list[str] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        calls.append(str(kwargs["message"]))
        if kwargs["message"] == "fail once":
            raise RuntimeError("boom")
        return TurnResult(
            run_id=73,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="handled later"),
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
                message=SimpleNamespace(id=7),
                raw_text="fail once",
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
                message=SimpleNamespace(id=8),
                raw_text="works after",
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

    assert calls == ["fail once", "works after"]
    assert len(delivery.calls) == 1
    assert delivery.calls[0]["text"] == "handled later"


async def test_telethon_user_service_rejects_duplicate_live_runtime_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only one Telethon runtime per transport/account should be allowed at a time."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_runtime_conflict.db'}",
    )
    await seed_profile_and_binding(settings)
    first_client = FakeTelethonClient()
    second_client = FakeTelethonClient()

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_telethon_credentials",
        fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.import_telethon",
        lambda: SimpleNamespace(events_module=SimpleNamespace(NewMessage=lambda **kwargs: kwargs)),
    )

    first = TelethonUserService(
        settings,
        endpoint=endpoint(reply_mode="disabled"),
        client_factory=lambda **kwargs: first_client,
    )
    second = TelethonUserService(
        settings,
        endpoint=endpoint(endpoint_id="telethon-second", reply_mode="disabled"),
        client_factory=lambda **kwargs: second_client,
    )

    await first.start()
    try:
        with pytest.raises(ChannelRuntimeLeaseRegistryError) as exc_info:
            await second.start()
    finally:
        await second.stop()
        await first.stop()

    assert exc_info.value.error_code == "channel_runtime_account_conflict"


async def test_telethon_user_service_stop_does_not_release_other_runtime_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopping a failed secondary runtime must not free the primary runtime lease."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_runtime_lease_owner.db'}",
    )
    await seed_profile_and_binding(settings)
    first_client = FakeTelethonClient()
    second_client = FakeTelethonClient()
    third_client = FakeTelethonClient()

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_telethon_credentials",
        fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.import_telethon",
        lambda: SimpleNamespace(events_module=SimpleNamespace(NewMessage=lambda **kwargs: kwargs)),
    )

    first = TelethonUserService(
        settings,
        endpoint=endpoint(reply_mode="disabled"),
        client_factory=lambda **kwargs: first_client,
    )
    second = TelethonUserService(
        settings,
        endpoint=endpoint(endpoint_id="telethon-second", reply_mode="disabled"),
        client_factory=lambda **kwargs: second_client,
    )
    third = TelethonUserService(
        settings,
        endpoint=endpoint(endpoint_id="telethon-third", reply_mode="disabled"),
        client_factory=lambda **kwargs: third_client,
    )

    await first.start()
    try:
        with pytest.raises(ChannelRuntimeLeaseRegistryError):
            await second.start()
        await second.stop()
        with pytest.raises(ChannelRuntimeLeaseRegistryError):
            await third.start()
    finally:
        await third.stop()
        await second.stop()
        await first.stop()
