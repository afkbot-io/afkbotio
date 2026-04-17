"""Unit tests for Task Flow runtime message rendering helpers."""

from __future__ import annotations

from afkbot.services.task_flow.message_factory import TaskMessageAttachment, compose_task_message


def _attachment(*, name: str, content_bytes: bytes, content_type: str | None = None) -> TaskMessageAttachment:
    return TaskMessageAttachment(
        id=f"att_{name}",
        name=name,
        content_type=content_type,
        kind="file",
        byte_size=len(content_bytes),
        sha256="deadbeef",
        content_bytes=content_bytes,
    )


def test_compose_task_message_renders_unknown_utf8_payloads_as_text() -> None:
    """Unknown UTF-8 attachments should still render inline when they look like text."""

    message = compose_task_message(
        "Review the attachment.",
        attachments=(
            _attachment(
                name="notes.custom",
                content_bytes="alpha\nbeta".encode("utf-8"),
                content_type=None,
            ),
        ),
    )

    assert "Task Attachments:" in message
    assert "Content:\nalpha\nbeta" in message


def test_compose_task_message_treats_unknown_binary_payloads_as_base64() -> None:
    """Unknown non-text payloads should not be dumped into the runtime prompt as raw text."""

    message = compose_task_message(
        "Review the attachment.",
        attachments=(
            _attachment(
                name="blob.bin",
                content_bytes=b"\x01\x02\x03\x04binary-ish payload",
                content_type=None,
            ),
        ),
    )

    assert "Binary content (base64):" in message
    assert "Content:\n" not in message
