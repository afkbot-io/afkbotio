"""Ingress, retry, and lifecycle tests for the Telethon user-channel runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channels import ChannelOutboundAttachment, ChannelOutboundMessage
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError
from afkbot.services.channels.endpoint_contracts import ChannelIngressBatchConfig
from afkbot.services.channels.ingress_coalescer import ChannelIngressBatch, ChannelIngressEvent
from afkbot.services.channels.ingress_persistence import get_channel_ingress_pending_service
from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.services.channels.telethon_user.normalization import (
    TelethonInboundMessage,
    TelethonUserIdentity,
)
from afkbot.services.channels.telethon_user.service import TelethonUserService
from afkbot.services.channels.telethon_user.service_events import enrich_inbound_with_downloaded_media
from afkbot.settings import Settings
from tests.services.channels.telethon_user._harness import (
    FakeDeliveryService,
    FakeFloodWaitError,
    FakeTelethonClient,
    endpoint,
    fake_resolve_credentials,
    seed_profile_and_binding,
)


async def test_telethon_user_service_translates_flood_wait_from_live_sender(
    tmp_path: Path,
) -> None:
    """Telethon live sender should surface FloodWait as structured retry metadata."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_flood_wait.db'}",
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )

    class FloodClient(FakeTelethonClient):
        async def send_message(self, entity: object, text: str) -> object:
            _ = entity, text
            raise FakeFloodWaitError(9)

    service._client = FloodClient()  # type: ignore[attr-defined]

    with pytest.raises(TelethonUserServiceError) as exc_info:
        await service._send_text_via_live_client(  # type: ignore[attr-defined]
            SimpleNamespace(
                peer_id="42",
                thread_id=None,
                to_payload=lambda: {"transport": "telegram_user", "account_id": "tg-user", "peer_id": "42"},
            ),
            "hello",
        )

    assert exc_info.value.error_code == "telethon_flood_wait"
    assert exc_info.value.metadata["retry_after_sec"] == 9


async def test_telethon_user_service_sends_rich_live_message(tmp_path: Path) -> None:
    """Telethon live sender should support text formatting, buttons, and file delivery."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_rich_send.db'}",
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )

    class RichClient(FakeTelethonClient):
        def __init__(self) -> None:
            super().__init__()
            self.sent_files: list[dict[str, object]] = []

        async def send_message(self, entity: object, text: str, **kwargs: object) -> object:
            self.sent_messages.append({"entity": entity, "text": text, **kwargs})
            return SimpleNamespace(id=91)

        async def send_file(self, entity: object, file: object, **kwargs: object) -> object:
            self.sent_files.append({"entity": entity, "file": file, **kwargs})
            return SimpleNamespace(id=92)

    client = RichClient()
    service._client = client  # type: ignore[attr-defined]
    report_path = tmp_path / "profiles/default/report.txt"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("report", encoding="utf-8")

    payload = await service._send_text_via_live_client(  # type: ignore[attr-defined]
        SimpleNamespace(
            peer_id="42",
            thread_id=None,
            to_payload=lambda: {"transport": "telegram_user", "account_id": "tg-user", "peer_id": "42"},
        ),
        ChannelOutboundMessage(
            text="*hello*",
            parse_mode="md",
            reply_markup={"inline_keyboard": [[{"text": "Open", "url": "https://example.com"}]]},
            attachments=(
                ChannelOutboundAttachment(kind="document", source="report.txt", caption="report"),
            ),
        ),
    )

    assert payload["message_id"] == 91
    assert Path(str(client.sent_files[0]["file"])) == report_path
    assert client.sent_files[0]["caption"] == "report"
    assert client.sent_messages[0]["text"] == "*hello*"
    assert client.sent_messages[0]["parse_mode"] == "md"
    assert client.sent_messages[0]["buttons"] is not None


async def test_telethon_user_service_rejects_attachment_paths_outside_workspace(
    tmp_path: Path,
) -> None:
    """Telethon userbot file sends should not read arbitrary host paths."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_rich_send_scope.db'}",
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("secret", encoding="utf-8")

    service._client = FakeTelethonClient()  # type: ignore[attr-defined]

    with pytest.raises(TelethonUserServiceError) as exc_info:
        await service._send_text_via_live_client(  # type: ignore[attr-defined]
            SimpleNamespace(
                peer_id="42",
                thread_id=None,
                to_payload=lambda: {"transport": "telegram_user", "account_id": "tg-user", "peer_id": "42"},
            ),
            ChannelOutboundMessage(
                attachments=(ChannelOutboundAttachment(kind="document", source=str(outside_file)),),
            ),
        )

    assert exc_info.value.error_code == "telethon_media_path_invalid"


async def test_telethon_user_service_splits_long_rich_text_after_file_send(tmp_path: Path) -> None:
    """Rich Telethon messages should keep text chunking when attachments are present."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_rich_send_chunks.db'}",
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )

    class RichClient(FakeTelethonClient):
        def __init__(self) -> None:
            super().__init__()
            self.sent_files: list[dict[str, object]] = []

        async def send_message(self, entity: object, text: str, **kwargs: object) -> object:
            self.sent_messages.append({"entity": entity, "text": text, **kwargs})
            return SimpleNamespace(id=len(self.sent_messages))

        async def send_file(self, entity: object, file: object, **kwargs: object) -> object:
            self.sent_files.append({"entity": entity, "file": file, **kwargs})
            return SimpleNamespace(id=90)

    client = RichClient()
    service._client = client  # type: ignore[attr-defined]
    report_path = tmp_path / "profiles/default/report.txt"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("report", encoding="utf-8")

    payload = await service._send_text_via_live_client(  # type: ignore[attr-defined]
        SimpleNamespace(
            peer_id="42",
            thread_id=None,
            to_payload=lambda: {"transport": "telegram_user", "account_id": "tg-user", "peer_id": "42"},
        ),
        ChannelOutboundMessage(
            text=("beta " * 900).strip(),
            attachments=(ChannelOutboundAttachment(kind="document", source="report.txt"),),
        ),
    )

    assert payload["message_id"] == 2
    assert len(client.sent_files) == 1
    assert len(client.sent_messages) == 2
    assert all(len(str(item["text"])) <= 4096 for item in client.sent_messages)


async def test_telethon_user_service_downloads_media_into_profile_workspace(tmp_path: Path) -> None:
    """Telethon inbound media should be saved where file tools can read it."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_media_download.db'}",
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )

    async def download_media(*, file: str) -> str:
        destination = Path(file)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / "note.txt"
        path.write_text("hello from telegram file", encoding="utf-8")
        return str(path)

    inbound = TelethonInboundMessage(
        event_key="tg-user:42:10",
        message_id=10,
        chat_id="42",
        chat_kind="private",
        user_id="777",
        text="read this",
    )
    event = SimpleNamespace(
        message=SimpleNamespace(
            document=object(),
            file=SimpleNamespace(mime_type="text/plain"),
        ),
        download_media=download_media,
    )

    enriched = await enrich_inbound_with_downloaded_media(service, event=event, inbound=inbound)

    assert "Downloaded Telegram attachments:" in enriched.text
    assert "channel_attachments/telegram_user/telethon-main/10/note.txt" in enriched.text
    assert "hello from telegram file" in enriched.text


async def test_telethon_user_service_skips_oversized_media_before_download(tmp_path: Path) -> None:
    """Telethon inbound media should honor the configured download limit before saving."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_media_prelimit.db'}",
        channel_media_download_max_bytes=4,
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )

    async def download_media(*, file: str) -> str:
        _ = file
        raise AssertionError("oversized media should not be downloaded")

    inbound = TelethonInboundMessage(
        event_key="tg-user:42:11",
        message_id=11,
        chat_id="42",
        chat_kind="private",
        user_id="777",
        text="too big",
    )
    event = SimpleNamespace(
        message=SimpleNamespace(
            document=object(),
            file=SimpleNamespace(mime_type="text/plain", size=5),
        ),
        download_media=download_media,
    )

    enriched = await enrich_inbound_with_downloaded_media(service, event=event, inbound=inbound)

    assert "download skipped" in enriched.text
    assert "file too large" in enriched.text


async def test_telethon_user_service_deletes_oversized_media_after_download(tmp_path: Path) -> None:
    """Telethon downloads with missing metadata should be removed if the saved file is too large."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_media_postlimit.db'}",
        channel_media_download_max_bytes=4,
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )
    saved_path: Path | None = None

    async def download_media(*, file: str) -> str:
        nonlocal saved_path
        destination = Path(file)
        destination.mkdir(parents=True, exist_ok=True)
        saved_path = destination / "large.txt"
        saved_path.write_text("12345", encoding="utf-8")
        return str(saved_path)

    inbound = TelethonInboundMessage(
        event_key="tg-user:42:12",
        message_id=12,
        chat_id="42",
        chat_kind="private",
        user_id="777",
        text="too big after save",
    )
    event = SimpleNamespace(
        message=SimpleNamespace(
            document=object(),
            file=SimpleNamespace(mime_type="text/plain"),
        ),
        download_media=download_media,
    )

    enriched = await enrich_inbound_with_downloaded_media(service, event=event, inbound=inbound)

    assert saved_path is not None
    assert not saved_path.exists()
    assert "download skipped" in enriched.text
    assert "file too large" in enriched.text


async def test_telethon_user_service_schedules_retry_when_batch_delivery_hits_flood_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FloodWait during batch delivery should defer retry instead of only logging a hard failure."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_batch_retry.db'}",
    )
    service = TelethonUserService(
        settings,
        endpoint=endpoint(
            ingress_batch=ChannelIngressBatchConfig(enabled=True, debounce_ms=100),
        ),
        client_factory=lambda **kwargs: FakeTelethonClient(),
    )
    scheduled: list[int] = []

    async def fake_schedule(*, retry_after_sec: int) -> None:
        scheduled.append(retry_after_sec)

    monkeypatch.setattr(service, "_schedule_pending_ingress_retry", fake_schedule)

    await service._handle_ingress_batch_error(  # type: ignore[attr-defined]
        ChannelIngressBatch(
            endpoint_id="telethon-main",
            transport="telegram_user",
            account_id="tg-user",
            peer_id="42",
            thread_id=None,
            user_id="777",
            chat_kind="private",
            events=(
                ChannelIngressEvent(
                    endpoint_id="telethon-main",
                    transport="telegram_user",
                    account_id="tg-user",
                    peer_id="42",
                    thread_id=None,
                    user_id="777",
                    event_key="tg-user:42:51",
                    message_id="51",
                    source_event_id="51",
                    text="persisted hello",
                    observed_at="2026-03-10T10:00:00+00:00",
                    chat_kind="private",
                ),
            ),
        ),
        ChannelDeliveryServiceError(
            error_code="telethon_flood_wait",
            reason="Retry later.",
            metadata={"retry_after_sec": 17},
        ),
    )

    assert scheduled == [17]


async def test_telethon_user_service_restores_pending_ingress_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted pending Telethon ingress batches should flush on runtime start."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_pending.db'}",
    )
    await seed_profile_and_binding(settings)
    pending_service = get_channel_ingress_pending_service(settings)
    await pending_service.record_pending(
        event=ChannelIngressEvent(
            endpoint_id="telethon-main",
            transport="telegram_user",
            account_id="tg-user",
            peer_id="42",
            thread_id=None,
            user_id="777",
            event_key="tg-user:42:51",
            message_id="51",
            source_event_id="51",
            text="persisted hello",
            observed_at="2026-03-10T10:00:00+00:00",
            chat_kind="private",
        )
    )
    captured: list[dict[str, object]] = []
    delivery = FakeDeliveryService()
    fake_client = FakeTelethonClient()

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_telethon_credentials",
        fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.import_telethon",
        lambda: SimpleNamespace(events_module=SimpleNamespace(NewMessage=lambda **kwargs: kwargs)),
    )

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=205,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="restored"),
        )

    service = TelethonUserService(
        settings,
        endpoint=endpoint(
            ingress_batch=ChannelIngressBatchConfig(enabled=True, debounce_ms=1500),
            reply_mode="same_chat",
        ),
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    await service.stop()

    assert len(captured) == 1
    assert captured[0]["message"] == "persisted hello"
    assert len(delivery.calls) == 1
    remaining = await pending_service.list_pending(endpoint_id="telethon-main")
    assert remaining == []


async def test_telethon_user_service_spills_queue_overflow_into_pending_backlog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue overflow should persist the inbound event for deferred retry instead of dropping it."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_queue_overflow.db'}",
    )
    await seed_profile_and_binding(settings)
    pending_service = get_channel_ingress_pending_service(settings)
    scheduled: list[int] = []
    service = TelethonUserService(
        settings,
        endpoint=endpoint(reply_mode="disabled"),
    )
    service._identity = TelethonUserIdentity(  # type: ignore[attr-defined]
        user_id=1001,
        username="afkme",
        phone="+79990000000",
        display_name="Afk Me",
    )
    service._queue = SimpleNamespace(  # type: ignore[assignment]
        put_nowait=lambda item: (_ for _ in ()).throw(asyncio.QueueFull()),
    )

    async def fake_retry(*, retry_after_sec: int) -> None:
        scheduled.append(retry_after_sec)

    monkeypatch.setattr(service, "_schedule_pending_ingress_retry", fake_retry)

    await service._on_new_message(  # type: ignore[attr-defined]
        SimpleNamespace(
            message=SimpleNamespace(id=9),
            raw_text="overflowed hello",
            out=False,
            is_private=True,
            is_group=False,
            chat_id=42,
            sender_id=777,
            is_reply=False,
        )
    )

    pending = await pending_service.list_pending(endpoint_id="telethon-main")
    assert len(pending) == 1
    assert pending[0].event_key == "tg-user:42:9"
    assert pending[0].text == "overflowed hello"
    assert scheduled == [1]


async def test_telethon_user_service_restores_pending_ingress_with_live_sender_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real channel delivery should see the live sender before pending restore flushes replies."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telethon_pending_live_sender.db'}",
    )
    await seed_profile_and_binding(settings)
    pending_service = get_channel_ingress_pending_service(settings)
    await pending_service.record_pending(
        event=ChannelIngressEvent(
            endpoint_id="telethon-main",
            transport="telegram_user",
            account_id="tg-user",
            peer_id="42",
            thread_id=None,
            user_id="777",
            event_key="tg-user:42:61",
            message_id="61",
            source_event_id="61",
            text="persisted hello",
            observed_at="2026-03-10T10:00:00+00:00",
            chat_kind="private",
        )
    )
    fake_client = FakeTelethonClient()

    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.resolve_telethon_credentials",
        fake_resolve_credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.channels.telethon_user.service.import_telethon",
        lambda: SimpleNamespace(events_module=SimpleNamespace(NewMessage=lambda **kwargs: kwargs)),
    )

    async def fake_run_chat_turn(**kwargs: object) -> TurnResult:
        return TurnResult(
            run_id=206,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="restored"),
        )

    service = TelethonUserService(
        settings,
        endpoint=endpoint(
            ingress_batch=ChannelIngressBatchConfig(enabled=True, debounce_ms=1500),
            reply_mode="same_chat",
        ),
        run_chat_turn_fn=fake_run_chat_turn,
        client_factory=lambda **kwargs: fake_client,
    )

    await service.start()
    await service.stop()

    assert fake_client.sent_messages == [{"entity": 42, "text": "restored"}]
    remaining = await pending_service.list_pending(endpoint_id="telethon-main")
    assert remaining == []
