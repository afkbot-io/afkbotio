"""Telegram polling routing and group-trigger tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channels.endpoint_contracts import ChannelAccessPolicy
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.channels.telegram_polling import TelegramPollingService
from afkbot.settings import Settings
from tests.services.channels.telegram_polling._harness import (
    FakeAppRuntime,
    FakeDeliveryService,
    endpoint,
    seed_profile_and_binding,
)


async def test_telegram_polling_requires_mention_in_group_chat(tmp_path: Path) -> None:
    """Group messages without direct mention or reply-to-bot should be ignored."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_group.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 55,
                "message": {
                    "message_id": 7,
                    "from": {"id": 888, "is_bot": False},
                    "chat": {"id": -100123, "type": "supergroup"},
                    "text": "hello everyone",
                },
            }
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
            envelope=ActionEnvelope(action="finalize", message="should not send"),
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
    assert captured == []


async def test_telegram_polling_all_messages_mode_processes_group_chat_without_mention(
    tmp_path: Path,
) -> None:
    """Group trigger mode `all_messages` should route plain group text without mention/reply."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_group_all.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 56,
                "message": {
                    "message_id": 8,
                    "from": {"id": 889, "is_bot": False},
                    "chat": {"id": -100124, "type": "supergroup"},
                    "text": "hello everyone",
                },
            }
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
            envelope=ActionEnvelope(action="finalize", message="handled"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(group_trigger_mode="all_messages"),
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
    assert captured[0]["message"] == "hello everyone"
    assert len(delivery.calls) == 1
    assert delivery.calls[0]["text"] == "handled"


async def test_telegram_polling_access_policy_drops_unlisted_private_sender(
    tmp_path: Path,
) -> None:
    """Endpoint access policy should block unlisted DMs before routing into AgentLoop."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_private_allowlist.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 57,
                "message": {
                    "message_id": 9,
                    "from": {"id": 67890, "is_bot": False},
                    "chat": {"id": 67890, "type": "private"},
                    "text": "run taskflow",
                },
            }
        ]
    )
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=94,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="should not send"),
        )

    service = TelegramPollingService(
        settings,
        endpoint=endpoint(
            access_policy=ChannelAccessPolicy(
                private_policy="allowlist",
                allow_from=("12345",),
            )
        ),
        state_path=get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main"),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
        run_chat_turn_fn=_fake_run_chat_turn,
    )

    processed = await service.poll_once()

    assert processed == 1
    assert captured == []
    assert delivery.calls == []


async def test_telegram_polling_strips_bot_mention_and_uses_thread_session(
    tmp_path: Path,
) -> None:
    """Mentioned topic message should strip bot mention and keep thread_id in reply target."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_topic.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 78,
                "message": {
                    "message_id": 8,
                    "message_thread_id": 9001,
                    "from": {"id": 999, "is_bot": False},
                    "chat": {"id": -100777, "type": "supergroup"},
                    "text": "@afkbot review this",
                },
            }
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
            envelope=ActionEnvelope(action="finalize", message="topic reply"),
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
    await service.poll_once()

    # Assert
    assert captured[0]["message"] == "review this"
    assert delivery.calls[0]["target"].model_dump(exclude_none=True) == {
        "transport": "telegram",
        "account_id": "telegram-bot",
        "peer_id": "-100777",
        "thread_id": "9001",
        "user_id": "999",
    }


async def test_telegram_polling_accepts_command_targeted_at_bot_in_group(tmp_path: Path) -> None:
    """Command-style group references should route after stripping only the bot suffix."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_command_target.db'}",
    )
    await seed_profile_and_binding(settings)
    app_runtime = FakeAppRuntime(
        updates=[
            {
                "update_id": 79,
                "message": {
                    "message_id": 9,
                    "from": {"id": 1000, "is_bot": False},
                    "chat": {"id": -100778, "type": "supergroup"},
                    "text": "/summarize@afkbot review this",
                },
            }
        ]
    )
    delivery = FakeDeliveryService()
    captured: list[dict[str, object]] = []

    async def _fake_run_chat_turn(**kwargs: object) -> TurnResult:
        captured.append(dict(kwargs))
        return TurnResult(
            run_id=94,
            profile_id=str(kwargs["profile_id"]),
            session_id=str(kwargs["session_id"]),
            envelope=ActionEnvelope(action="finalize", message="group command handled"),
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
    await service.poll_once()

    # Assert
    assert captured[0]["message"] == "/summarize review this"
    assert delivery.calls[0]["text"] == "group command handled"


async def test_telegram_polling_propagates_non_no_match_binding_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected binding failures must not be downgraded to a soft no-match."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'telegram_binding_error.db'}",
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
    service = TelegramPollingService(
        settings,
        endpoint=endpoint(),
        state_path=get_channel_endpoint_service(settings).telegram_polling_state_path(endpoint_id="telegram-main"),
        app_runtime=app_runtime,
        channel_delivery_service=delivery,  # type: ignore[arg-type]
    )

    async def _broken_resolve(**kwargs: object) -> object:
        _ = kwargs
        raise ChannelBindingServiceError(
            error_code="channel_binding_profile_not_found",
            reason="Profile disappeared.",
        )

    monkeypatch.setattr(
        "afkbot.services.channels.telegram_polling.resolve_runtime_target",
        _broken_resolve,
    )

    # Act / Assert
    with pytest.raises(ChannelBindingServiceError, match="Profile disappeared"):
        await service.poll_once()
