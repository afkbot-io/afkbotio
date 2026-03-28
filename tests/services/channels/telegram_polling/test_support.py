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
