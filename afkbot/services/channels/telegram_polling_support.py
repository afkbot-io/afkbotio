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
class TelegramInboundMessage:
    """Normalized inbound Telegram message text plus attachment summary."""

    update_id: int
    chat_id: str
    chat_type: str
    user_id: str
    text: str
    thread_id: str | None = None


def extract_inbound_message(
    *,
    update: dict[str, object],
    identity: TelegramBotIdentity | None,
    group_trigger_mode: TelegramGroupTriggerMode = "mention_or_reply",
) -> TelegramInboundMessage | None:
    """Normalize one Telegram update into inbound text message payload."""

    message = update.get("message")
    if not isinstance(message, dict):
        return None
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
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
    attachment_summary = _render_attachment_summary(message)
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


def _render_attachment_summary(message: dict[str, object]) -> str | None:
    parts: list[str] = []
    photo_payload = message.get("photo")
    if isinstance(photo_payload, list) and photo_payload:
        parts.append("photo attached")
    document_payload = message.get("document")
    if isinstance(document_payload, dict):
        parts.append(_describe_document_payload(document_payload))
    if not parts:
        return None
    return "Incoming Telegram attachments:\n" + "\n".join(f"- {item}" for item in parts)


def _describe_document_payload(document_payload: dict[str, object]) -> str:
    file_name = document_payload.get("file_name")
    mime_type = document_payload.get("mime_type")
    file_size = document_payload.get("file_size")
    details: list[str] = []
    if isinstance(file_name, str) and file_name.strip():
        details.append(file_name.strip())
    if isinstance(mime_type, str) and mime_type.strip():
        details.append(mime_type.strip())
    if isinstance(file_size, int) and file_size > 0:
        details.append(f"{file_size} bytes")
    if details:
        return "document: " + ", ".join(details)
    return "document attached"


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
