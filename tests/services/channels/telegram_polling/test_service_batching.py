"""Telegram polling batching, retry, and offset-tracking tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channels.endpoint_contracts import ChannelIngressBatchConfig
from afkbot.services.channels.ingress_coalescer import ChannelIngressEvent
from afkbot.services.channels.ingress_persistence import get_channel_ingress_pending_service
from afkbot.services.channels.telegram_polling import TelegramPollingService
from afkbot.settings import Settings
from tests.services.channels.telegram_polling._harness import (
    FakeAppRuntime,
    FakeDeliveryService,
    default_state_path,
    endpoint,
    load_state_payload,
    seed_profile_and_binding,
)


async def test_telegram_polling_restores_pending_ingress_after_restart(tmp_path: Path) -> None:
    """Persisted pending ingress batches should flush before polling new updates after restart."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_polling_pending.db'}",
    )
    await seed_profile_and_binding(settings)
    pending_service = get_channel_ingress_pending_service(settings)
    await pending_service.record_pending(
        event=ChannelIngressEvent(
            endpoint_id="telegram-main",
            transport="telegram",
            account_id="telegram-bot",
            peer_id="42",
            thread_id=None,
            user_id="777",
            event_key="41",
            message_id="41",
            source_event_id="41",
            text="hello from persisted batch",
            observed_at="2026-03-10T10:00:00+00:00",
            chat_kind="private",
        )
    )
    app_runtime = FakeAppRuntime(updates=[])
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=93,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="replayed"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(
            ingress_batch=ChannelIngressBatchConfig(enabled=True, debounce_ms=1500),
        ),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 0
    assert len(captured) == 1
    assert captured[0]["message"] == "hello from persisted batch"
    assert len(delivery.calls) == 1
    remaining = await pending_service.list_pending(endpoint_id="telegram-main")
    assert remaining == []
    assert load_state_payload(settings=settings)["next_update_offset"] == 42


async def test_telegram_polling_batches_sequential_messages_when_ingress_batch_enabled(
    tmp_path: Path,
) -> None:
    """Polling adapter should merge sequential messages into one turn when batching is enabled."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_polling_batch.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 41,
                "message": {
                    "message_id": 5,
                    "from": {"id": 777, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "first",
                },
            },
            {
                "update_id": 42,
                "message": {
                    "message_id": 6,
                    "from": {"id": 777, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "second",
                },
            },
        ]
    )
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=93,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="batched reply"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(
            ingress_batch=ChannelIngressBatchConfig(
                enabled=True,
                debounce_ms=100,
                max_batch_size=10,
                max_buffer_chars=4000,
            )
        ),
        state_path=default_state_path(settings=settings),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 2
    assert len(captured) == 1
    assert "Multiple recent messages arrived from the same conversation." in str(captured[0]["message"])
    assert "first" in str(captured[0]["message"])
    assert "second" in str(captured[0]["message"])
    assert captured[0]["client_msg_id"] == "telegram-batch:telegram-bot:42:-:777:41:42:2"
    context_overrides = captured[0]["context_overrides"]
    assert context_overrides.runtime_metadata["channel_ingress_batch"]["message_count"] == 2
    assert len(delivery.calls) == 1


async def test_telegram_polling_defers_persisted_offset_until_batched_flush(tmp_path: Path) -> None:
    """Background batching should keep the persisted offset behind until the batch flushes."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_polling_batch_ack.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 41,
                "message": {
                    "message_id": 5,
                    "from": {"id": 777, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "first",
                },
            },
            {
                "update_id": 42,
                "message": {
                    "message_id": 6,
                    "from": {"id": 777, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "second",
                },
            },
        ]
    )
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []
    state_path = default_state_path(settings=settings)

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=94,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="batched reply"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(
            ingress_batch=ChannelIngressBatchConfig(
                enabled=True,
                debounce_ms=60_000,
                max_batch_size=10,
                max_buffer_chars=4000,
            )
        ),
        state_path=state_path,
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )
    sleeper = asyncio.create_task(asyncio.sleep(60))
    service._task = sleeper  # type: ignore[attr-defined]

    # Act
    try:
        processed = await service.poll_once()
        assert processed == 2
        assert captured == []
        assert not state_path.exists()
        await service._ingress_coalescer.flush_all()  # type: ignore[attr-defined]
    finally:
        service._task = None  # type: ignore[attr-defined]
        sleeper.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sleeper

    # Assert
    assert len(captured) == 1
    assert json.loads(state_path.read_text(encoding="utf-8"))["next_update_offset"] == 43


async def test_telegram_polling_loads_persisted_offset_before_polling(tmp_path: Path) -> None:
    """Polling adapter should reuse persisted offset when fetching updates."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_state.db'}",
    )
    state_path = default_state_path(settings=settings)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps({"next_update_offset": 321}, ensure_ascii=True),
        encoding="utf-8",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(updates=[])
    delivery = FakeDeliveryService()

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        raise AssertionError(f"Unexpected turn execution: {kwargs}")

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(),
        state_path=state_path,
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 0
    get_updates_call = next(call for call in app_runtime.calls if call["action"] == "get_updates")
    assert get_updates_call["params"]["offset"] == 321


async def test_telegram_polling_ignores_stale_offset_from_other_account(tmp_path: Path) -> None:
    """Persisted offset must not be reused after polling account changes."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_state_mismatch.db'}",
    )
    state_path = default_state_path(settings=settings)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {"next_update_offset": 999999, "account_id": "telegram-bot-a"},
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(updates=[])
    delivery = FakeDeliveryService()

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        raise AssertionError(f"Unexpected turn execution: {kwargs}")

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(account_id="telegram-bot-b"),
        state_path=state_path,
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 0
    get_updates_call = next(call for call in app_runtime.calls if call["action"] == "get_updates")
    assert get_updates_call["params"]["offset"] is None


async def test_telegram_polling_retries_failed_update_without_advancing_offset(
    tmp_path: Path,
) -> None:
    """Transient turn failures should keep the current update on retry."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_retry.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 41,
                "message": {
                    "message_id": 5,
                    "from": {"id": 777, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "hello bot",
                },
            }
        ]
    )
    delivery = FakeDeliveryService()
    attempts = 0

    async def _flaky_run_chat_turn(**kwargs: object) -> TurnResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return TurnResult(
            run_id=91,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="hello user"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(),
        state_path=default_state_path(settings=settings),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_flaky_run_chat_turn,
    )

    # Act
    with pytest.raises(RuntimeError, match="temporary failure"):
        await service.poll_once()
    processed_second = await service.poll_once()

    # Assert
    assert processed_second == 1
    assert attempts == 2
    assert len(delivery.calls) == 1
    assert load_state_payload(settings=settings)["next_update_offset"] == 42


async def test_telegram_polling_skips_extract_failure_and_continues_batch(
    tmp_path: Path,
) -> None:
    """One malformed update should not block later updates in the same batch."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_poison.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 41,
                "message": {
                    "message_id": 5,
                    "from": {"id": 777, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "first update",
                },
            },
            {
                "update_id": 42,
                "message": {
                    "message_id": 6,
                    "from": {"id": 778, "is_bot": False},
                    "chat": {"id": 43, "type": "private"},
                    "text": "second update",
                },
            },
        ]
    )
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=92,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="ok"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(),
        state_path=default_state_path(settings=settings),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )
    original_extract = service._extract_inbound_message  # type: ignore[attr-defined]
    failed_once = False

    def _flaky_extract(update: dict[str, object]) -> object | None:
        nonlocal failed_once
        if update.get("update_id") == 41 and not failed_once:
            failed_once = True
            raise RuntimeError("bad update payload")
        return original_extract(update)

    service._extract_inbound_message = _flaky_extract  # type: ignore[method-assign,assignment]

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 2
    assert len(captured) == 1
    assert captured[0]["message"] == "second update"
    assert load_state_payload(settings=settings)["next_update_offset"] == 43
