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
