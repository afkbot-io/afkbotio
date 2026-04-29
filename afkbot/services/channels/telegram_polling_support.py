"""Normalization and persistence helpers for Telegram Bot API polling."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_LOGGER = logging.getLogger(__name__)
TelegramGroupTriggerMode = Literal["mention_or_reply", "reply_only", "mention_only", "all_messages"]


@dataclass(frozen=True, slots=True)
class TelegramBotIdentity:
    """Resolved Telegram bot identity used for mention/reply filtering."""

    bot_id: int
    username: str | None


@dataclass(frozen=True, slots=True)
class TelegramInboundAttachment:
    """Downloadable media attached to one Telegram Bot API update."""

    kind: str
    file_id: str
    file_unique_id: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    emoji: str | None = None
    is_animated: bool = False
    is_video: bool = False


@dataclass(frozen=True, slots=True)
class TelegramInboundMessage:
    """Normalized inbound Telegram message text plus attachment summary."""

    update_id: int
    chat_id: str
    chat_type: str
    user_id: str
    text: str
    thread_id: str | None = None
    attachments: tuple[TelegramInboundAttachment, ...] = ()
    callback_query_id: str | None = None


def extract_inbound_message(
    *,
    update: dict[str, object],
    identity: TelegramBotIdentity | None,
    group_trigger_mode: TelegramGroupTriggerMode = "mention_or_reply",
) -> TelegramInboundMessage | None:
    """Normalize one Telegram update into inbound text message payload."""

    message = update.get("message")
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return None
    callback_query = update.get("callback_query")
    if isinstance(callback_query, dict):
        return _extract_callback_query(
            update_id=update_id,
            callback_query=callback_query,
        )
    if not isinstance(message, dict):
        return None
    from_payload = message.get("from")
    if not isinstance(from_payload, dict):
        return None
    if bool(from_payload.get("is_bot")):
        return None
    user_id = from_payload.get("id")
    if not isinstance(user_id, int):
        return None
    chat_payload = message.get("chat")
    if not isinstance(chat_payload, dict):
        return None
    chat_id = chat_payload.get("id")
    if not isinstance(chat_id, int):
        return None
    chat_type = str(chat_payload.get("type") or "").strip().lower()
    content_text = _extract_message_content_text(message)
    attachments = _extract_message_attachments(message)
    attachment_summary = _render_attachment_summary(message, attachments=attachments)
    trigger_text = content_text or ""
    if not trigger_text and not attachment_summary:
        return None
    if not should_process_message(
        message=message,
        text=trigger_text,
        chat_type=chat_type,
        identity=identity,
        group_trigger_mode=group_trigger_mode,
    ):
        return None
    thread_id_raw = message.get("message_thread_id")
    thread_id = str(thread_id_raw) if isinstance(thread_id_raw, int) else None
    stripped_text = strip_bot_reference(text=trigger_text, identity=identity) if trigger_text else ""
    normalized_text = _compose_inbound_text(text=stripped_text, attachment_summary=attachment_summary)
    if not normalized_text:
        return None
    return TelegramInboundMessage(
        update_id=update_id,
        chat_id=str(chat_id),
        chat_type=chat_type,
        user_id=str(user_id),
        thread_id=thread_id,
        text=normalized_text,
        attachments=attachments,
    )


def _extract_callback_query(
    *,
    update_id: int,
    callback_query: dict[str, object],
) -> TelegramInboundMessage | None:
    callback_id = callback_query.get("id")
    if not isinstance(callback_id, str) or not callback_id.strip():
        return None
    from_payload = callback_query.get("from")
    if not isinstance(from_payload, dict) or bool(from_payload.get("is_bot")):
        return None
    user_id = from_payload.get("id")
    if not isinstance(user_id, int):
        return None
    message = callback_query.get("message")
    if not isinstance(message, dict):
        return None
    chat_payload = message.get("chat")
    if not isinstance(chat_payload, dict):
        return None
    chat_id = chat_payload.get("id")
    if not isinstance(chat_id, int):
        return None
    chat_type = str(chat_payload.get("type") or "").strip().lower() or "private"
    data = callback_query.get("data")
    game_short_name = callback_query.get("game_short_name")
    payload_label = "callback_data"
    payload_value: str | None = None
    if isinstance(data, str) and data.strip():
        payload_value = data.strip()
    elif isinstance(game_short_name, str) and game_short_name.strip():
        payload_label = "game_short_name"
        payload_value = game_short_name.strip()
    if payload_value is None:
        return None
    button_text = _find_callback_button_text(message=message, callback_data=payload_value)
    original_text = _extract_message_content_text(message)
    lines = ["Telegram button pressed:"]
    if button_text:
        lines.append(f"- button: {button_text}")
    lines.append(f"- {payload_label}: {payload_value}")
    if original_text:
        lines.append(f"- original message: {original_text}")
    thread_id_raw = message.get("message_thread_id")
    thread_id = str(thread_id_raw) if isinstance(thread_id_raw, int) else None
    return TelegramInboundMessage(
        update_id=update_id,
        chat_id=str(chat_id),
        chat_type=chat_type,
        user_id=str(user_id),
        text="\n".join(lines),
        thread_id=thread_id,
        callback_query_id=callback_id.strip(),
    )


def should_process_message(
    *,
    message: dict[str, object],
    text: str,
    chat_type: str,
    identity: TelegramBotIdentity | None,
    group_trigger_mode: TelegramGroupTriggerMode = "mention_or_reply",
) -> bool:
    """Return whether inbound Telegram text should be routed to AgentLoop."""

    if identity is None:
        return False
    if chat_type == "private":
        return True
    if group_trigger_mode == "all_messages":
        return True
    reply_to_message = message.get("reply_to_message")
    reply_to_bot = False
    if isinstance(reply_to_message, dict):
        reply_from = reply_to_message.get("from")
        if isinstance(reply_from, dict) and reply_from.get("id") == identity.bot_id:
            reply_to_bot = True
    if group_trigger_mode == "reply_only":
        return reply_to_bot
    mention = contains_bot_reference(text=text, identity=identity)
    if group_trigger_mode == "mention_only":
        return mention
    return reply_to_bot or mention


def contains_bot_reference(*, text: str, identity: TelegramBotIdentity | None) -> bool:
    """Return whether text contains bot mention or command-style reference."""

    if identity is None or not identity.username:
        return False
    username = re.escape(identity.username)
    return bool(
        re.search(rf"(?<![\w/])@{username}\b", text, flags=re.IGNORECASE)
        or re.search(rf"/[A-Za-z0-9_]+@{username}\b", text, flags=re.IGNORECASE)
    )


def strip_bot_reference(*, text: str, identity: TelegramBotIdentity | None) -> str:
    """Strip bot mention from inbound message text before sending to the model."""

    if identity is None or not identity.username:
        return text.strip()
    username = re.escape(identity.username)
    command_target_pattern = re.compile(rf"(/[\w]+)@{username}\b", re.IGNORECASE)
    normalized = command_target_pattern.sub(r"\1", text)
    mention_pattern = re.compile(rf"@{username}\b", re.IGNORECASE)
    return mention_pattern.sub("", normalized).strip()


def _extract_message_content_text(message: dict[str, object]) -> str | None:
    for key in ("text", "caption"):
        value = message.get(key)
        if isinstance(value, str):
            normalized = value.strip()
            if normalized:
                return normalized
    return None


def _render_attachment_summary(
    message: dict[str, object],
    *,
    attachments: tuple[TelegramInboundAttachment, ...] | None = None,
) -> str | None:
    parts: list[str] = []
    attachment_items = attachments if attachments is not None else _extract_message_attachments(message)
    for attachment in attachment_items:
        parts.append(_describe_attachment(attachment))
    photo_payload = message.get("photo")
    if isinstance(photo_payload, list) and photo_payload and not any(
        item.kind == "photo" for item in attachment_items
    ):
        parts.append("photo attached")
    sticker_payload = message.get("sticker")
    if isinstance(sticker_payload, dict) and not any(item.kind == "sticker" for item in attachment_items):
        parts.append(_describe_sticker_payload(sticker_payload))
    animation_payload = message.get("animation")
    if isinstance(animation_payload, dict) and not any(item.kind == "animation" for item in attachment_items):
        parts.append(_describe_media_payload("animation", animation_payload))
    for payload_key, label in (
        ("video", "video"),
        ("audio", "audio"),
        ("voice", "voice"),
        ("video_note", "video note"),
    ):
        payload = message.get(payload_key)
        if isinstance(payload, dict) and not any(item.kind == payload_key for item in attachment_items):
            parts.append(_describe_media_payload(label, payload))
    document_payload = message.get("document")
    if isinstance(document_payload, dict) and not any(item.kind == "document" for item in attachment_items):
        parts.append(_describe_document_payload(document_payload))
    if not parts:
        return None
    return "Incoming Telegram attachments:\n" + "\n".join(f"- {item}" for item in parts)


def _describe_document_payload(document_payload: dict[str, object]) -> str:
    return _describe_media_payload("document", document_payload, fallback="document attached")


def _describe_sticker_payload(sticker_payload: dict[str, object]) -> str:
    details: list[str] = []
    emoji = sticker_payload.get("emoji")
    set_name = sticker_payload.get("set_name")
    if isinstance(emoji, str) and emoji.strip():
        details.append(emoji.strip())
    if isinstance(set_name, str) and set_name.strip():
        details.append(set_name.strip())
    if bool(sticker_payload.get("is_animated")):
        details.append("animated")
    if bool(sticker_payload.get("is_video")):
        details.append("video")
    return "sticker: " + ", ".join(details) if details else "sticker attached"


def _describe_media_payload(
    label: str,
    media_payload: dict[str, object],
    *,
    fallback: str | None = None,
) -> str:
    file_name = media_payload.get("file_name")
    mime_type = media_payload.get("mime_type")
    file_size = media_payload.get("file_size")
    duration = media_payload.get("duration")
    details: list[str] = []
    if isinstance(file_name, str) and file_name.strip():
        details.append(file_name.strip())
    if isinstance(duration, int) and duration > 0:
        details.append(f"{duration}s")
    if isinstance(mime_type, str) and mime_type.strip():
        details.append(mime_type.strip())
    if isinstance(file_size, int) and file_size > 0:
        details.append(f"{file_size} bytes")
    if details:
        return f"{label}: " + ", ".join(details)
    return fallback or f"{label} attached"


def _extract_message_attachments(message: dict[str, object]) -> tuple[TelegramInboundAttachment, ...]:
    attachments: list[TelegramInboundAttachment] = []
    photo_payload = message.get("photo")
    if isinstance(photo_payload, list) and photo_payload:
        selected = _select_largest_photo_payload(photo_payload)
        if selected is not None:
            attachment = _attachment_from_payload("photo", selected)
            if attachment is not None:
                attachments.append(attachment)
    for payload_key, kind in (
        ("sticker", "sticker"),
        ("animation", "animation"),
        ("video", "video"),
        ("audio", "audio"),
        ("voice", "voice"),
        ("video_note", "video_note"),
        ("document", "document"),
    ):
        payload = message.get(payload_key)
        if isinstance(payload, dict):
            attachment = _attachment_from_payload(kind, payload)
            if attachment is not None:
                attachments.append(attachment)
    return tuple(attachments)


def _attachment_from_payload(
    kind: str,
    payload: dict[str, object],
) -> TelegramInboundAttachment | None:
    file_id = payload.get("file_id")
    if not isinstance(file_id, str) or not file_id.strip():
        return None
    return TelegramInboundAttachment(
        kind=kind,
        file_id=file_id.strip(),
        file_unique_id=_optional_text(payload.get("file_unique_id")),
        file_name=_optional_text(payload.get("file_name")),
        mime_type=_optional_text(payload.get("mime_type")),
        file_size=_optional_int(payload.get("file_size")),
        duration=_optional_int(payload.get("duration")),
        width=_optional_int(payload.get("width")),
        height=_optional_int(payload.get("height")),
        emoji=_optional_text(payload.get("emoji")),
        is_animated=_optional_bool(payload.get("is_animated")),
        is_video=_optional_bool(payload.get("is_video")),
    )


def _select_largest_photo_payload(items: list[object]) -> dict[str, object] | None:
    candidates = [item for item in items if isinstance(item, dict)]
    if not candidates:
        return None

    def _score(payload: dict[str, object]) -> int:
        file_size = payload.get("file_size")
        if isinstance(file_size, int) and file_size > 0:
            return file_size
        width = payload.get("width")
        height = payload.get("height")
        if isinstance(width, int) and isinstance(height, int):
            return max(0, width) * max(0, height)
        return 0

    return max(candidates, key=_score)


def _describe_attachment(attachment: TelegramInboundAttachment) -> str:
    label = "video note" if attachment.kind == "video_note" else attachment.kind
    if attachment.kind == "sticker" and attachment.emoji:
        return _join_details(
            "sticker",
            (
                attachment.emoji,
                attachment.file_name,
                attachment.mime_type,
                attachment.file_size,
                "animated" if attachment.is_animated else None,
                "video" if attachment.is_video else None,
            ),
        )
    details: list[str | int | None] = []
    if attachment.file_name:
        details.append(attachment.file_name)
    if attachment.duration is not None and attachment.duration > 0:
        details.append(f"{attachment.duration}s")
    if attachment.mime_type:
        details.append(attachment.mime_type)
    if attachment.file_size is not None and attachment.file_size > 0:
        details.append(f"{attachment.file_size} bytes")
    if attachment.kind == "photo" and attachment.width and attachment.height:
        details.insert(0, f"{attachment.width}x{attachment.height}")
    if details:
        return f"{label}: " + ", ".join(str(item) for item in details if item is not None)
    return f"{label} attached"


def _join_details(label: str, values: tuple[object, ...]) -> str:
    details = [str(item).strip() for item in values if item not in {None, ""}]
    return f"{label}: " + ", ".join(details) if details else f"{label} attached"


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _optional_bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def _find_callback_button_text(
    *,
    message: dict[str, object],
    callback_data: str,
) -> str | None:
    reply_markup = message.get("reply_markup")
    if not isinstance(reply_markup, dict):
        return None
    keyboard = reply_markup.get("inline_keyboard")
    if not isinstance(keyboard, list):
        return None
    for row in keyboard:
        if not isinstance(row, list):
            continue
        for button in row:
            if not isinstance(button, dict):
                continue
            if button.get("callback_data") != callback_data:
                continue
            text = button.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
    return None


def _compose_inbound_text(*, text: str, attachment_summary: str | None) -> str:
    normalized_text = text.strip()
    if attachment_summary:
        if normalized_text:
            return f"{normalized_text}\n\n{attachment_summary}"
        return attachment_summary
    return normalized_text


def load_next_update_offset(*, state_path: Path, account_id: str) -> int | None:
    """Load persisted polling offset when it belongs to the current account."""

    if not state_path.exists():
        return None
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _LOGGER.warning("telegram_polling_state_load_failed path=%s", state_path)
        return None
    stored_account_id = payload.get("account_id")
    if (
        isinstance(stored_account_id, str)
        and stored_account_id.strip()
        and stored_account_id.strip() != account_id
    ):
        _LOGGER.info(
            "telegram_polling_state_account_mismatch path=%s stored_account_id=%s current_account_id=%s",
            state_path,
            stored_account_id.strip(),
            account_id,
        )
        return None
    value = payload.get("next_update_offset")
    return value if isinstance(value, int) and value >= 0 else None


def persist_next_update_offset(
    *,
    state_path: Path,
    account_id: str,
    next_update_offset: int,
) -> None:
    """Persist next Telegram update offset atomically."""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = Path(f"{state_path}.tmp")
    tmp_path.write_text(
        json.dumps(
            {"next_update_offset": next_update_offset, "account_id": account_id},
            ensure_ascii=True,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    tmp_path.replace(state_path)
