"""Telegram polling delivery and reply-behavior tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channels.endpoint_contracts import (
    ChannelIngressBatchConfig,
    ChannelReplyHumanizationConfig,
)
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.channels.telegram_polling import TelegramPollingService
from afkbot.settings import Settings
from tests.services.channels.telegram_polling._harness import (
    FakeAppRuntime,
    FakeDeliveryService,
    FlakyTimeoutingDeliveryService,
    TimeoutingChatActionAppRuntime,
    TimeoutingGetUpdatesAppRuntime,
    endpoint,
    seed_profile_and_binding,
)


async def test_telegram_polling_processes_private_message_and_replies(tmp_path: Path) -> None:
    """Polling adapter should route private text messages through bindings and reply."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_polling.db'}",
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
    captured: list[dict[str, object]] = []

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=91,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="hello user"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(),
        state_path=get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main"),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 1
    assert len(captured) == 1
    assert captured[0]["profile_id"] == "default"
    assert captured[0]["message"] == "hello bot"
    assert captured[0]["client_msg_id"] == "telegram:telegram-bot:41"
    assert len(delivery.calls) == 1
    assert delivery.calls[0]["text"] == "hello user"
    assert delivery.calls[0]["credential_profile_key"] == "bot-main"
    assert delivery.calls[0]["target"].model_dump(exclude_none=True) == {
        "transport": "telegram",
        "account_id": "telegram-bot",
        "peer_id": "42",
        "user_id": "777",
    }


async def test_telegram_polling_suppresses_llm_error_replies(tmp_path: Path) -> None:
    """Transient LLM failures should not be echoed through Telegram Bot API replies."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_polling_llm_error.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 42,
                "message": {
                    "message_id": 6,
                    "from": {"id": 777, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "hello bot",
                },
            }
        ]
    )
    delivery = FakeDeliveryService()

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        _ = kwargs
        return TurnResult(
            run_id=92,
            profile_id="default",
            session_id="telegram:42",
            envelope=ActionEnvelope(
                action="finalize",
                message="LLM provider is temporarily unavailable. Please try again shortly.",
                spec_patch={"error_code": "llm_provider_network_error"},
            ),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(),
        state_path=get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main"),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 1
    assert delivery.calls == []


async def test_telegram_polling_emits_typing_action_before_reply_when_humanization_enabled(
    tmp_path: Path,
) -> None:
    """Telegram polling replies should show typing before the actual send when enabled."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_polling_humanize.db'}",
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

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        return TurnResult(
            run_id=91,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="hello user"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(
            reply_humanization=ChannelReplyHumanizationConfig(
                enabled=True,
                min_delay_ms=0,
                max_delay_ms=1,
                chars_per_second=120,
            )
        ),
        state_path=get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main"),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 1
    chat_action_call = next(call for call in app_runtime.calls if call["action"] == "send_chat_action")
    assert chat_action_call["params"] == {
        "chat_id": "42",
        "action": "typing",
    }
    assert len(delivery.calls) == 1


async def test_telegram_polling_ignores_typing_timeout_and_still_delivers_reply(
    tmp_path: Path,
) -> None:
    """Typing indicator timeouts should not block the actual Telegram reply."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_polling_typing_timeout.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = TimeoutingChatActionAppRuntime(
        updates=[
            {
                "update_id": 40,
                "message": {
                    "message_id": 4,
                    "from": {"id": 777, "is_bot": False},
                    "chat": {"id": 42, "type": "private"},
                    "text": "hello bot",
                },
            }
        ]
    )
    delivery = FakeDeliveryService()

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        return TurnResult(
            run_id=191,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="hello user"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(
            reply_humanization=ChannelReplyHumanizationConfig(
                enabled=True,
                min_delay_ms=0,
                max_delay_ms=1,
                chars_per_second=120,
            )
        ),
        state_path=get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main"),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 1
    assert any(call["action"] == "send_chat_action" for call in app_runtime.calls)
    assert len(delivery.calls) == 1


async def test_telegram_polling_retries_delivery_timeout_in_process(tmp_path: Path) -> None:
    """One transient Telegram delivery timeout should be retried without restart."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_polling_retry.db'}",
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
    delivery = FlakyTimeoutingDeliveryService()

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        return TurnResult(
            run_id=291,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="hello retry"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(
            ingress_batch=ChannelIngressBatchConfig(
                enabled=True,
                debounce_ms=100,
                cooldown_sec=0,
            )
        ),
        state_path=get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main"),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )
    service._next_delivery_retry_delay_sec = lambda conversation_key: 0.01  # type: ignore[method-assign,assignment]

    # Act
    processed = await service.poll_once()
    await asyncio.sleep(0.05)

    # Assert
    assert processed == 1
    assert delivery.attempts >= 2
    assert len(delivery.calls) == 1


async def test_telegram_polling_does_not_send_non_finalize_envelopes(tmp_path: Path) -> None:
    """Only finalized envelopes should be sent back to Telegram."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_non_finalize.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 101,
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

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        return TurnResult(
            run_id=91,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="request_secure_field", message="secret please"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(),
        state_path=get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main"),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 1
    assert delivery.calls == []


async def test_telegram_polling_treats_get_updates_timeout_as_idle(tmp_path: Path) -> None:
    """Long-poll transport timeouts should behave like an empty batch, not a hard failure."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_timeout_idle.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = TimeoutingGetUpdatesAppRuntime(updates=[])
    delivery = FakeDeliveryService()
    service = TelegramPollingService(
        settings,
        endpoint=endpoint(),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
    )

    # Act
    processed = await service.poll_once()

    # Assert
    assert processed == 0
    assert delivery.calls == []
