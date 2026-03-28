"""Inbound event normalization helpers for the Telethon user-channel runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from afkbot.services.channels.telethon_user.normalization import (
    TelethonInboundMessage,
    build_telethon_inbound_text,
    normalize_invocation_text,
    should_process_group_message,
)
from afkbot.services.channels.telethon_user.runtime_support import event_replies_to_user
from afkbot.services.channels.telethon_user.watcher import (
    build_entity_match_text,
    build_event_chat_match_text,
    matches_chat_title_filters,
)

if TYPE_CHECKING:
    from afkbot.services.channels.telethon_user.service import TelethonUserService


async def on_new_message(service: TelethonUserService, *, event: object) -> None:
    """Normalize one incoming Telethon event and push it into watcher and ingress paths."""

    watched_event = await service._normalize_watched_event(event)
    if watched_event is not None:
        await service._buffer_watched_event(watched_event)
    inbound = await service._normalize_event(event)
    if inbound is None:
        return
    queued_event = service.queued_event_cls(
        event_key=inbound.event_key,
        message_id=inbound.message_id,
        chat_id=inbound.chat_id,
        chat_kind=inbound.chat_kind,
        user_id=inbound.user_id,
        thread_id=inbound.thread_id,
        text=inbound.text,
        observed_at=datetime.now(UTC).isoformat(),
        is_self_command=inbound.is_self_command,
    )
    try:
        service._queue.put_nowait(queued_event)
    except service.queue_full_error:
        await service._spill_overflow_event(queued_event)


async def normalize_event(
    service: TelethonUserService,
    *,
    event: object,
) -> TelethonInboundMessage | None:
    """Normalize one reactive Telethon event into an AgentLoop ingress payload."""

    identity = service._identity
    if identity is None:
        return None
    message = getattr(event, "message", None)
    message_id = getattr(message, "id", None)
    if not isinstance(message_id, int):
        return None
    text = build_telethon_inbound_text(event=event).strip()
    if not text:
        return None
    chat_match_text = await resolve_reactive_chat_match_text(event=event)
    if not matches_chat_title_filters(
        title=chat_match_text,
        blocked_patterns=service._endpoint.reply_blocked_chat_patterns,
        allowed_patterns=service._endpoint.reply_allowed_chat_patterns,
    ):
        return None
    event_key = f"{service._endpoint.account_id}:{getattr(event, 'chat_id', '')}:{message_id}"
    sender_id = getattr(event, "sender_id", None)
    user_id = str(sender_id) if sender_id is not None else None
    chat_id = str(getattr(event, "chat_id"))
    is_outgoing = bool(getattr(event, "out", False))
    if is_outgoing:
        if not service._endpoint.process_self_commands:
            return None
        if not text.startswith(service._endpoint.command_prefix):
            return None
        normalized = normalize_invocation_text(
            text=text,
            identity=identity,
            command_prefix=service._endpoint.command_prefix,
        )
        if not normalized:
            return None
        return TelethonInboundMessage(
            event_key=event_key,
            message_id=message_id,
            chat_id=chat_id,
            chat_kind="private" if bool(getattr(event, "is_private", False)) else "group",
            user_id=user_id,
            text=normalized,
            thread_id=None,
            is_self_command=True,
        )
    if bool(getattr(event, "is_private", False)):
        return TelethonInboundMessage(
            event_key=event_key,
            message_id=message_id,
            chat_id=chat_id,
            chat_kind="private",
            user_id=user_id,
            text=text,
            thread_id=None,
        )
    if not bool(getattr(event, "is_group", False)):
        return None
    reply_to_self = await event_replies_to_user(event, user_id=identity.user_id)
    if not should_process_group_message(
        text=text,
        identity=identity,
        group_invocation_mode=service._endpoint.group_invocation_mode,
        command_prefix=service._endpoint.command_prefix,
        reply_to_self=reply_to_self,
    ):
        return None
    normalized = normalize_invocation_text(
        text=text,
        identity=identity,
        command_prefix=service._endpoint.command_prefix,
    )
    if not normalized:
        normalized = text
    return TelethonInboundMessage(
        event_key=event_key,
        message_id=message_id,
        chat_id=chat_id,
        chat_kind="group",
        user_id=user_id,
        text=normalized,
        thread_id=None,
    )


async def resolve_reactive_chat_match_text(*, event: object) -> str:
    """Resolve best-effort match text for one reactive inbound event before routing."""

    match_text = build_event_chat_match_text(event)
    fallback_chat_id = getattr(event, "chat_id", None)
    if match_text not in {"unknown-chat", str(fallback_chat_id)}:
        return match_text
    getter_names = ("get_chat", "get_sender") if bool(getattr(event, "is_private", False)) else ("get_chat",)
    for getter_name in getter_names:
        getter = getattr(event, getter_name, None)
        if not callable(getter):
            continue
        try:
            entity = await getter()
        except Exception:
            continue
        resolved = build_entity_match_text(entity, fallback_id=None)
        if resolved != "unknown-chat":
            return resolved
    return match_text
