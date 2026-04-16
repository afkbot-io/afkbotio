"""Helpers for composing Task Flow runtime messages and session ids."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass

_INLINE_TEXT_ATTACHMENT_CHAR_LIMIT = 12000
_INLINE_BINARY_ATTACHMENT_BASE64_LIMIT = 8192


@dataclass(frozen=True, slots=True)
class TaskMessageAttachment:
    """Attachment payload rendered into the detached runtime prompt."""

    id: str
    name: str
    content_type: str | None
    kind: str
    byte_size: int
    sha256: str
    content_bytes: bytes


def task_session_id(*, task_id: str) -> str:
    """Build deterministic child session id for one task."""

    return f"taskflow:{task_id}"


def compose_task_message(
    description: str,
    *,
    attachments: Sequence[TaskMessageAttachment] = (),
) -> str:
    """Compose one detached task description for AgentLoop."""

    parts = [description.strip()]
    rendered_attachments = [_render_attachment_block(attachment) for attachment in attachments]
    rendered_attachments = [item for item in rendered_attachments if item]
    if rendered_attachments:
        parts.extend(
            [
                "",
                "Task Attachments:",
                "Use the attachment contents below as additional task context.",
                "",
                "\n\n".join(rendered_attachments),
            ]
        )
    return "\n".join(part for part in parts if part).strip()


def _render_attachment_block(attachment: TaskMessageAttachment) -> str:
    """Render one attachment into a bounded prompt-friendly text block."""

    header = (
        f"Attachment: {attachment.name} "
        f"(kind={attachment.kind}, content_type={attachment.content_type or 'application/octet-stream'}, "
        f"bytes={attachment.byte_size}, sha256={attachment.sha256})"
    )
    rendered_content = _render_attachment_content(attachment)
    if not rendered_content:
        return header
    return f"{header}\n{rendered_content}"


def _render_attachment_content(attachment: TaskMessageAttachment) -> str:
    """Render inline attachment content when it is text-like or compact enough."""

    content_bytes = bytes(attachment.content_bytes or b"")
    if not content_bytes:
        return "Content: [empty file]"
    if _attachment_is_text_like(attachment=attachment):
        text_value = content_bytes.decode("utf-8", errors="replace").strip()
        if not text_value:
            return "Content: [empty text after decoding]"
        if len(text_value) > _INLINE_TEXT_ATTACHMENT_CHAR_LIMIT:
            return (
                "Content:\n"
                f"{text_value[:_INLINE_TEXT_ATTACHMENT_CHAR_LIMIT].rstrip()}\n"
                "[truncated]"
            )
        return f"Content:\n{text_value}"
    encoded = base64.b64encode(content_bytes).decode("ascii")
    if len(encoded) > _INLINE_BINARY_ATTACHMENT_BASE64_LIMIT:
        encoded = f"{encoded[:_INLINE_BINARY_ATTACHMENT_BASE64_LIMIT].rstrip()}...[truncated]"
    return f"Binary content (base64):\n{encoded}"


def _attachment_is_text_like(*, attachment: TaskMessageAttachment) -> bool:
    """Return whether one attachment should be rendered as decoded text."""

    normalized_type = str(attachment.content_type or "").strip().lower()
    if normalized_type.startswith("image/") or normalized_type in {
        "application/pdf",
        "application/zip",
        "application/octet-stream",
    }:
        return False
    if normalized_type.startswith("text/") or normalized_type in {
        "application/json",
        "application/xml",
        "application/javascript",
    }:
        return True
    return b"\x00" not in bytes(attachment.content_bytes or b"")
