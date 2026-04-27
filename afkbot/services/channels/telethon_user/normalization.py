"""Pure helpers for normalizing Telethon user-channel events."""

from __future__ import annotations

import re
from dataclasses import dataclass

from afkbot.services.channels.endpoint_contracts import TelethonGroupInvocationMode


@dataclass(frozen=True, slots=True)
class TelethonUserIdentity:
    """Resolved Telegram user identity for the connected Telethon session."""

    user_id: int
    username: str | None
    phone: str | None
    display_name: str | None


@dataclass(frozen=True, slots=True)
class TelethonInboundMessage:
    """Normalized inbound text message routed from Telethon into AgentLoop."""

    event_key: str
    message_id: int
    chat_id: str
    chat_kind: str
    user_id: str | None
    text: str
    thread_id: str | None = None
    is_self_command: bool = False


def should_process_group_message(
    *,
    text: str,
    identity: TelethonUserIdentity,
    group_invocation_mode: TelethonGroupInvocationMode,
    command_prefix: str,
    reply_to_self: bool,
) -> bool:
    """Return whether one group message should trigger a userbot turn."""

    if group_invocation_mode == "all_messages":
        return True
    command_like = is_command_or_mention(
        text=text,
        identity=identity,
        command_prefix=command_prefix,
    )
    if group_invocation_mode == "reply_only":
        return reply_to_self
    if group_invocation_mode == "command_only":
        return command_like
    return reply_to_self or command_like


def is_command_or_mention(
    *,
    text: str,
    identity: TelethonUserIdentity,
    command_prefix: str,
) -> bool:
    """Return true when text addresses the userbot explicitly."""

    normalized = text.strip()
    if not normalized:
        return False
    if normalized.startswith(command_prefix):
        return True
    if not identity.username:
        return False
    return f"@{identity.username.lower()}" in normalized.lower()


def normalize_invocation_text(
    *,
    text: str,
    identity: TelethonUserIdentity,
    command_prefix: str,
) -> str:
    """Strip the explicit command prefix or user mention before routing text."""

    normalized = text.strip()
    if not normalized:
        return ""
    if normalized.startswith(command_prefix):
        normalized = normalized.removeprefix(command_prefix).strip()
    if identity.username:
        pattern = re.compile(rf"@{re.escape(identity.username)}\b", re.IGNORECASE)
        normalized = pattern.sub("", normalized).strip()
    return normalized


def build_telethon_inbound_text(*, event: object) -> str:
    """Return inbound text plus attachment summary for one Telethon event."""

    base_text = ""
    raw_text = getattr(event, "raw_text", None)
    if isinstance(raw_text, str):
        base_text = raw_text.strip()
    attachment_summary = render_telethon_attachment_summary(event=event)
    if attachment_summary:
        if base_text:
            return f"{base_text}\n\n{attachment_summary}"
        return attachment_summary
    return base_text


def render_telethon_attachment_summary(*, event: object) -> str | None:
    """Render one compact attachment summary for the current Telethon message."""

    message = getattr(event, "message", None)
    if message is None:
        return None
    parts: list[str] = []
    if getattr(message, "photo", None) is not None:
        parts.append("photo attached")
    if bool(getattr(message, "sticker", False)):
        parts.append(_describe_telethon_sticker(getattr(message, "file", None)))
    if bool(getattr(message, "gif", False)):
        parts.append("animation/GIF attached")
    if bool(getattr(message, "video", False)):
        parts.append("video attached")
    if bool(getattr(message, "audio", False)):
        parts.append("audio attached")
    if bool(getattr(message, "voice", False)):
        parts.append("voice message attached")
    if bool(getattr(message, "round", False)):
        parts.append("video note attached")
    file_payload = getattr(message, "file", None)
    document_payload = getattr(message, "document", None)
    if file_payload is not None or document_payload is not None:
        parts.append(_describe_telethon_file(file_payload))
    if not parts:
        return None
    return "Incoming Telegram attachments:\n" + "\n".join(f"- {item}" for item in parts)


def _describe_telethon_sticker(file_payload: object | None) -> str:
    emoji = getattr(file_payload, "emoji", None)
    if isinstance(emoji, str) and emoji.strip():
        return f"sticker: {emoji.strip()}"
    return "sticker attached"


def _describe_telethon_file(file_payload: object | None) -> str:
    details: list[str] = []
    file_name = getattr(file_payload, "name", None)
    mime_type = getattr(file_payload, "mime_type", None)
    file_size = getattr(file_payload, "size", None)
    if isinstance(file_name, str) and file_name.strip():
        details.append(file_name.strip())
    if isinstance(mime_type, str) and mime_type.strip():
        details.append(mime_type.strip())
    if isinstance(file_size, int) and file_size > 0:
        details.append(f"{file_size} bytes")
    if details:
        return "document: " + ", ".join(details)
    return "document attached"
