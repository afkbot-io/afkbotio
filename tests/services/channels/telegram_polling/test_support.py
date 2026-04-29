"""Telegram polling support-behavior tests."""

from __future__ import annotations

from afkbot.services.channels.telegram_polling_support import (
    TelegramBotIdentity,
    extract_inbound_message,
)
from afkbot.services.channels.telegram_polling_runtime import _suggest_attachment_filename


def test_extract_inbound_message_includes_document_attachment_summary() -> None:
    """Document-only Telegram updates should still produce useful model input."""

    # Arrange
    update = {
        "update_id": 55,
        "message": {
            "message_id": 9,
            "from": {"id": 777, "is_bot": False},
            "chat": {"id": 42, "type": "private"},
            "document": {
                "file_name": "brief.pdf",
                "mime_type": "application/pdf",
                "file_size": 2048,
            },
            "caption": "посмотри",
        },
    }

    # Act
    inbound = extract_inbound_message(
        update=update,
        identity=TelegramBotIdentity(bot_id=1001, username="afkbot"),
    )

    # Assert
    assert inbound is not None
    assert inbound.text == (
        "посмотри\n\n"
        "Incoming Telegram attachments:\n"
        "- document: brief.pdf, application/pdf, 2048 bytes"
    )


def test_extract_inbound_message_describes_rich_media() -> None:
    """Telegram updates should describe stickers, GIFs, and voice notes for the model."""

    update = {
        "update_id": 56,
        "message": {
            "message_id": 10,
            "from": {"id": 777, "is_bot": False},
            "chat": {"id": 42, "type": "private"},
            "sticker": {"emoji": "👍", "set_name": "ok-pack", "is_animated": True},
            "animation": {
                "file_name": "party.gif",
                "mime_type": "image/gif",
                "file_size": 4096,
            },
            "voice": {"duration": 3, "mime_type": "audio/ogg", "file_size": 1024},
        },
    }

    inbound = extract_inbound_message(
        update=update,
        identity=TelegramBotIdentity(bot_id=1001, username="afkbot"),
    )

    assert inbound is not None
    assert inbound.text == (
        "Incoming Telegram attachments:\n"
        "- sticker: 👍, ok-pack, animated\n"
        "- animation: party.gif, image/gif, 4096 bytes\n"
        "- voice: 3s, audio/ogg, 1024 bytes"
    )


def test_extract_inbound_message_exposes_downloadable_media() -> None:
    """Downloadable Telegram media should keep file ids for later ingestion."""

    update = {
        "update_id": 57,
        "message": {
            "message_id": 11,
            "from": {"id": 777, "is_bot": False},
            "chat": {"id": 42, "type": "private"},
            "voice": {
                "file_id": "voice-file-id",
                "file_unique_id": "voice-unique",
                "duration": 5,
                "mime_type": "audio/ogg",
                "file_size": 2048,
            },
            "photo": [
                {"file_id": "small-photo", "file_unique_id": "small", "width": 90, "height": 90},
                {
                    "file_id": "large-photo",
                    "file_unique_id": "large",
                    "width": 1280,
                    "height": 720,
                    "file_size": 65536,
                },
            ],
        },
    }

    inbound = extract_inbound_message(
        update=update,
        identity=TelegramBotIdentity(bot_id=1001, username="afkbot"),
    )

    assert inbound is not None
    assert [item.kind for item in inbound.attachments] == ["photo", "voice"]
    assert inbound.attachments[0].file_id == "large-photo"
    assert inbound.attachments[1].file_id == "voice-file-id"


def test_extract_inbound_callback_query_routes_button_press() -> None:
    """Inline callback button presses should become model-facing inbound events."""

    update = {
        "update_id": 58,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 777, "is_bot": False},
            "data": "approve:42",
            "message": {
                "message_id": 12,
                "chat": {"id": 42, "type": "private"},
                "text": "Approve deployment?",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "Approve", "callback_data": "approve:42"},
                            {"text": "Reject", "callback_data": "reject:42"},
                        ]
                    ]
                },
            },
        },
    }

    inbound = extract_inbound_message(
        update=update,
        identity=TelegramBotIdentity(bot_id=1001, username="afkbot"),
    )

    assert inbound is not None
    assert inbound.callback_query_id == "callback-1"
    assert inbound.chat_id == "42"
    assert inbound.user_id == "777"
    assert inbound.text == (
        "Telegram button pressed:\n"
        "- button: Approve\n"
        "- callback_data: approve:42\n"
        "- original message: Approve deployment?"
    )


def test_extract_inbound_callback_query_preserves_forum_thread_id() -> None:
    """Forum topic callback presses should keep topic routing metadata."""

    update = {
        "update_id": 59,
        "callback_query": {
            "id": "callback-topic-1",
            "from": {"id": 777, "is_bot": False},
            "data": "approve:topic",
            "message": {
                "message_id": 13,
                "message_thread_id": 333,
                "chat": {"id": -10042, "type": "supergroup"},
                "text": "Approve topic?",
                "reply_markup": {
                    "inline_keyboard": [[{"text": "Approve", "callback_data": "approve:topic"}]]
                },
            },
        },
    }

    inbound = extract_inbound_message(
        update=update,
        identity=TelegramBotIdentity(bot_id=1001, username="afkbot"),
    )

    assert inbound is not None
    assert inbound.thread_id == "333"


def test_extract_inbound_sticker_keeps_animation_format_flags() -> None:
    """Downloaded sticker filenames need the Telegram sticker format flags."""

    update = {
        "update_id": 60,
        "message": {
            "message_id": 14,
            "from": {"id": 777, "is_bot": False},
            "chat": {"id": 42, "type": "private"},
            "sticker": {
                "file_id": "animated-sticker",
                "file_unique_id": "animated-unique",
                "emoji": "👍",
                "is_animated": True,
                "is_video": False,
            },
        },
    }

    inbound = extract_inbound_message(
        update=update,
        identity=TelegramBotIdentity(bot_id=1001, username="afkbot"),
    )

    assert inbound is not None
    assert inbound.attachments[0].is_animated is True
    assert inbound.attachments[0].is_video is False
    assert _suggest_attachment_filename(inbound.attachments[0]).endswith(".tgs")
