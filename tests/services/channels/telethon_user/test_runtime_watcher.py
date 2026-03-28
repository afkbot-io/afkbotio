"""Watcher-specific tests for the Telethon user-channel runtime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channel_routing import ChannelBindingService
from afkbot.services.channels import ChannelDeliveryTarget
from afkbot.services.channels.endpoint_contracts import TelethonWatcherConfig
from afkbot.services.channels.telethon_user.service import TelethonUserService
from afkbot.services.channels.telethon_user.watcher import watcher_memory_peer_id
from afkbot.settings import Settings
from tests.services.channels.telethon_user._harness import (
    FakeDeliveryService,
    FakeTelethonClient,
    endpoint,
    fake_dialog,
    fake_resolve_credentials,
    seed_profile_and_binding,
    watched_event,
)


async def test_telethon_user_service_watcher_collects_channel_posts_and_sends_digest_to_saved_messages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watcher mode should batch unmuted channel posts and deliver digests to Saved Messages."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_watch.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient(
        dialogs=[fake_dialog(chat_id=500, chat_kind="channel", title="Alpha Channel")]
    )
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=81,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="digest ready"),
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
            reply_mode="disabled",
            watcher=TelethonWatcherConfig(
                enabled=True,
                batch_interval_sec=300,
                dialog_refresh_interval_sec=300,
            ),
        ),
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        assert fake_client.handler is not None
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=11),
                raw_text="Important watcher update",
                out=False,
                is_private=False,
                is_group=False,
                chat_id=500,
                sender_id=900,
                is_reply=False,
            )
        )
        await service._flush_watcher_batch()  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert len(captured) == 1
    assert captured[0]["profile_id"] == "default"
    assert captured[0]["session_id"] == "telegram_user_watch:telethon-main"
    assert captured[0]["context_overrides"].runtime_metadata["transport"] == "telegram_user"
    assert captured[0]["context_overrides"].runtime_metadata["account_id"] == "tg-user"
    assert captured[0]["context_overrides"].runtime_metadata["peer_id"] == watcher_memory_peer_id("telethon-main")
    assert captured[0]["context_overrides"].runtime_metadata["channel_binding"] == {
        "binding_id": "telethon-main",
        "session_policy": "per-chat",
    }
    assert "Alpha Channel" in str(captured[0]["message"])
    assert "Important watcher update" in str(captured[0]["message"])
    assert fake_client.sent_messages == [{"entity": "me", "text": "digest ready"}]


async def test_telethon_user_service_watcher_falls_back_to_endpoint_profile_without_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watcher mode should keep working without a binding by falling back to the endpoint profile/session."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_watch_fallback.db'}",
    )
    await seed_profile_and_binding(settings)
    bindings = ChannelBindingService(settings)
    try:
        await bindings.delete(binding_id="telethon-main")
    finally:
        await bindings.shutdown()
    fake_client = FakeTelethonClient(
        dialogs=[fake_dialog(chat_id=505, chat_kind="channel", title="Fallback Channel")]
    )
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=85,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="fallback digest"),
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
            reply_mode="disabled",
            watcher=TelethonWatcherConfig(
                enabled=True,
                batch_interval_sec=300,
                dialog_refresh_interval_sec=300,
            ),
        ),
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        assert fake_client.handler is not None
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=16),
                raw_text="Fallback watcher update",
                out=False,
                is_private=False,
                is_group=False,
                chat_id=505,
                sender_id=905,
                is_reply=False,
            )
        )
        await service._flush_watcher_batch()  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert len(captured) == 1
    assert captured[0]["profile_id"] == "default"
    assert captured[0]["session_id"] == "telegram_user_watch:telethon-main"
    assert captured[0]["context_overrides"].runtime_metadata["peer_id"] == watcher_memory_peer_id("telethon-main")
    assert "channel_binding" not in captured[0]["context_overrides"].runtime_metadata
    assert fake_client.sent_messages == [{"entity": "me", "text": "fallback digest"}]


async def test_telethon_user_service_watcher_skips_muted_and_blocked_dialogs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watcher mode must ignore muted dialogs and title-blocklisted dialogs."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_watch_filters.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient(
        dialogs=[
            fake_dialog(
                chat_id=501,
                chat_kind="channel",
                title="Muted Channel",
                muted_until=datetime.now(UTC) + timedelta(hours=1),
            ),
            fake_dialog(chat_id=502, chat_kind="group", title="Spam Room"),
        ]
    )
    captured: list[dict[str, object]] = []

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=82,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="should not happen"),
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
            reply_mode="disabled",
            watcher=TelethonWatcherConfig(
                enabled=True,
                batch_interval_sec=300,
                dialog_refresh_interval_sec=300,
                blocked_chat_patterns=("spam",),
            ),
        ),
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        assert fake_client.handler is not None
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=12),
                raw_text="Muted content",
                out=False,
                is_private=False,
                is_group=False,
                chat_id=501,
                sender_id=901,
                is_reply=False,
            )
        )
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=13),
                raw_text="Blocked content",
                out=False,
                is_private=False,
                is_group=True,
                chat_id=502,
                sender_id=902,
                is_reply=False,
            )
        )
        await service._flush_watcher_batch()  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert captured == []
    assert fake_client.sent_messages == []


async def test_telethon_user_service_watcher_suppresses_no_digest_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watcher mode should drop batches whose result explicitly says NO_DIGEST."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_watch_no_digest.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient(
        dialogs=[fake_dialog(chat_id=503, chat_kind="group", title="Project Group")]
    )

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        return TurnResult(
            run_id=83,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="NO_DIGEST"),
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
            reply_mode="disabled",
            watcher=TelethonWatcherConfig(
                enabled=True,
                batch_interval_sec=300,
                dialog_refresh_interval_sec=300,
            ),
        ),
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    try:
        assert fake_client.handler is not None
        await fake_client.handler(
            SimpleNamespace(
                message=SimpleNamespace(id=14),
                raw_text="Routine chatter",
                out=False,
                is_private=False,
                is_group=True,
                chat_id=503,
                sender_id=903,
                is_reply=False,
            )
        )
        await service._flush_watcher_batch()  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert fake_client.sent_messages == []
    assert service._watcher_buffer == []  # type: ignore[attr-defined]


async def test_telethon_user_service_watcher_can_deliver_via_explicit_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Watcher digests should support explicit outbound targets such as Telegram bot delivery."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_user_watch_delivery.db'}",
    )
    await seed_profile_and_binding(settings)
    fake_client = FakeTelethonClient(
        dialogs=[fake_dialog(chat_id=504, chat_kind="channel", title="Delivery Channel")]
    )
    delivery = FakeDeliveryService()

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        return TurnResult(
            run_id=84,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="digest via bot"),
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
            reply_mode="disabled",
            watcher=TelethonWatcherConfig(
                enabled=True,
                batch_interval_sec=300,
                dialog_refresh_interval_sec=300,
                delivery_target=ChannelDeliveryTarget(
                    transport="telegram",
                    peer_id="999",
                ),
                delivery_credential_profile_key="bot-main",
            ),
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
                message=SimpleNamespace(id=15),
                raw_text="Ship it",
                out=False,
                is_private=False,
                is_group=False,
                chat_id=504,
                sender_id=904,
                is_reply=False,
            )
        )
        await service._flush_watcher_batch()  # type: ignore[attr-defined]
    finally:
        await service.stop()

    assert len(delivery.calls) == 1
    assert delivery.calls[0]["text"] == "digest via bot"
    assert delivery.calls[0]["credential_profile_key"] == "bot-main"
    assert delivery.calls[0]["target"].model_dump(exclude_none=True) == {
        "transport": "telegram",
        "peer_id": "999",
    }


async def test_telethon_user_service_watcher_dedupes_inflight_events(
    tmp_path: Path,
) -> None:
    """Watcher must not re-buffer one event while the same key is already in-flight."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_inflight.db'}",
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(
            watcher=TelethonWatcherConfig(
                enabled=True,
                max_batch_size=5,
                max_buffer_size=5,
            ),
            reply_mode="disabled",
        ),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )
    event = watched_event(event_key="watch:tg-user:42:10", chat_id="42", message_id=10)

    await service._buffer_watched_event(event)  # type: ignore[attr-defined]
    batch = await service._pop_watcher_batch()  # type: ignore[attr-defined]
    assert batch == (event,)

    await service._buffer_watched_event(event)  # type: ignore[attr-defined]

    assert service._watcher_buffer == []  # type: ignore[attr-defined]
    assert event.event_key in service._watcher_inflight_keys  # type: ignore[attr-defined]


async def test_telethon_user_service_restore_respects_max_buffer_size(
    tmp_path: Path,
) -> None:
    """Restore path must preserve the configured max buffer cap."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_restore.db'}",
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(
            watcher=TelethonWatcherConfig(
                enabled=True,
                max_batch_size=2,
                max_buffer_size=2,
            ),
            reply_mode="disabled",
        ),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )
    batch = (
        watched_event(event_key="watch:tg-user:42:10", chat_id="42", message_id=10),
        watched_event(event_key="watch:tg-user:42:11", chat_id="42", message_id=11),
    )
    existing = watched_event(event_key="watch:tg-user:43:12", chat_id="43", message_id=12)

    service._watcher_buffer = [existing]  # type: ignore[attr-defined]
    service._watcher_buffer_keys = {existing.event_key}  # type: ignore[attr-defined]
    service._watcher_inflight_keys = {item.event_key for item in batch}  # type: ignore[attr-defined]

    await service._restore_watcher_batch(batch)  # type: ignore[attr-defined]

    assert len(service._watcher_buffer) == 2  # type: ignore[attr-defined]
    assert len(service._watcher_buffer_keys) == 2  # type: ignore[attr-defined]
    assert service._watcher_inflight_keys == set()  # type: ignore[attr-defined]
