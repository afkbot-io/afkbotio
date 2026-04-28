"""Telegram polling support-behavior tests."""

from __future__ import annotations

from afkbot.services.channels.telegram_polling_support import (
    TelegramBotIdentity,
    extract_inbound_message,
)


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
