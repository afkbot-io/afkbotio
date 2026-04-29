"""Normalization-focused tests for the Telethon user-channel runtime."""

from __future__ import annotations

from types import SimpleNamespace

from afkbot.services.channels.telethon_user.normalization import build_telethon_inbound_text


def test_build_telethon_inbound_text_includes_attachment_summary() -> None:
    """Telethon inbound text should preserve caption text and append attachment metadata."""

    event = SimpleNamespace(
        raw_text="переведи",
        message=SimpleNamespace(
            photo=object(),
            document=object(),
            file=SimpleNamespace(name="photo.png", mime_type="image/png", size=4096),
        ),
    )

    assert build_telethon_inbound_text(event=event) == (
        "переведи\n\n"
        "Incoming Telegram attachments:\n"
        "- photo attached\n"
        "- document: photo.png, image/png, 4096 bytes"
    )


def test_build_telethon_inbound_text_describes_rich_media() -> None:
    """Telethon inbound text should expose sticker/GIF/voice hints to the model."""

    event = SimpleNamespace(
        raw_text="",
        message=SimpleNamespace(
            sticker=True,
            gif=True,
            voice=True,
            file=SimpleNamespace(
                name="reaction.webp",
                mime_type="image/webp",
                size=2048,
                emoji="🔥",
            ),
        ),
    )

    assert build_telethon_inbound_text(event=event) == (
        "Incoming Telegram attachments:\n"
        "- sticker: 🔥\n"
        "- animation/GIF attached\n"
        "- voice message attached\n"
        "- document: reaction.webp, image/webp, 2048 bytes"
    )
