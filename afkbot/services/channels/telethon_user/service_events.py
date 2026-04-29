"""Inbound event normalization helpers for the Telethon user-channel runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from afkbot.services.channels.access_policy import is_channel_message_allowed
from afkbot.services.channels.media_ingest import (
    build_channel_attachment_dir,
    build_text_preview,
    relative_to_profile_workspace,
)
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
    if not is_channel_message_allowed(
        policy=service._endpoint.access_policy,
        chat_kind=inbound.chat_kind,
        peer_id=inbound.chat_id,
        user_id=inbound.user_id,
    ):
        service.logger.warning(
            "telethon_user_access_denied account_id=%s peer_id=%s user_id=%s chat_kind=%s",
            service._endpoint.account_id,
            inbound.chat_id,
            inbound.user_id,
            inbound.chat_kind,
        )
        return
    inbound = await enrich_inbound_with_downloaded_media(service, event=event, inbound=inbound)
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


async def enrich_inbound_with_downloaded_media(
    service: TelethonUserService,
    *,
    event: object,
    inbound: TelethonInboundMessage,
) -> TelethonInboundMessage:
    """Download Telethon media to the profile workspace when the client exposes it."""

    downloader = getattr(event, "download_media", None)
    message = getattr(event, "message", None)
    if not callable(downloader) and message is not None:
        downloader = getattr(message, "download_media", None)
    if not callable(downloader):
        return inbound
    if not _telethon_event_has_media(event=event):
        return inbound
    max_bytes = service._settings.channel_media_download_max_bytes
    expected_size = _telethon_file_size(event=event)
    if expected_size is not None and expected_size > max_bytes:
        return _append_media_download_status(
            inbound=inbound,
            status=f"download skipped (file too large: {expected_size} > {max_bytes} bytes)",
        )
    destination = build_channel_attachment_dir(
        settings=service._settings,
        profile_id=service._endpoint.profile_id,
        transport=service._endpoint.transport,
        endpoint_id=service._endpoint.endpoint_id,
        event_id=str(inbound.message_id),
    )
    try:
        downloaded = await downloader(file=str(destination))
    except Exception as exc:
        service.logger.warning(
            "telethon_media_download_failed endpoint_id=%s message_id=%s exc=%s",
            service._endpoint.endpoint_id,
            inbound.message_id,
            f"{exc.__class__.__name__}: {exc}",
        )
        return inbound
    path = _coerce_downloaded_path(downloaded)
    if path is None:
        return inbound
    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = None
    if isinstance(size_bytes, int) and size_bytes > max_bytes:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            service.logger.warning(
                "telethon_media_oversized_delete_failed endpoint_id=%s message_id=%s path=%s",
                service._endpoint.endpoint_id,
                inbound.message_id,
                path,
            )
        return _append_media_download_status(
            inbound=inbound,
            status=f"download skipped (file too large: {size_bytes} > {max_bytes} bytes)",
        )
    relative_path = relative_to_profile_workspace(
        settings=service._settings,
        profile_id=service._endpoint.profile_id,
        path=path,
    )
    mime_type = _telethon_mime_type(event=event)
    lines = [f"- media: {relative_path}"]
    if mime_type:
        lines[0] += f", {mime_type}"
    if isinstance(size_bytes, int):
        lines[0] += f", {size_bytes} bytes"
    preview = build_text_preview(
        path=path,
        mime_type=mime_type,
        max_bytes=service._settings.channel_media_text_preview_bytes,
    )
    if preview is not None:
        preview_text, truncated = preview
        suffix = " [truncated]" if truncated else ""
        lines.append(f"  text preview{suffix}:\n{preview_text}")
    text = f"{inbound.text}\n\nDownloaded Telegram attachments:\n" + "\n".join(lines)
    return TelethonInboundMessage(
        event_key=inbound.event_key,
        message_id=inbound.message_id,
        chat_id=inbound.chat_id,
        chat_kind=inbound.chat_kind,
        user_id=inbound.user_id,
        text=text,
        thread_id=inbound.thread_id,
        is_self_command=inbound.is_self_command,
    )


def _append_media_download_status(
    *,
    inbound: TelethonInboundMessage,
    status: str,
) -> TelethonInboundMessage:
    text = f"{inbound.text}\n\nDownloaded Telegram attachments:\n- media: {status}"
    return TelethonInboundMessage(
        event_key=inbound.event_key,
        message_id=inbound.message_id,
        chat_id=inbound.chat_id,
        chat_kind=inbound.chat_kind,
        user_id=inbound.user_id,
        text=text,
        thread_id=inbound.thread_id,
        is_self_command=inbound.is_self_command,
    )


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


def _telethon_event_has_media(*, event: object) -> bool:
    message = getattr(event, "message", None)
    if message is None:
        return False
    return any(
        bool(getattr(message, attr_name, False))
        for attr_name in (
            "photo",
            "document",
            "sticker",
            "gif",
            "video",
            "audio",
            "voice",
            "round",
        )
    )


def _coerce_downloaded_path(value: object) -> Path | None:
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value.strip():
        return Path(value.strip())
    return None


def _telethon_mime_type(*, event: object) -> str | None:
    message = getattr(event, "message", None)
    file_payload = getattr(message, "file", None)
    mime_type = getattr(file_payload, "mime_type", None)
    if isinstance(mime_type, str) and mime_type.strip():
        return mime_type.strip()
    return None


def _telethon_file_size(*, event: object) -> int | None:
    message = getattr(event, "message", None)
    for payload in (
        getattr(message, "file", None),
        getattr(message, "document", None),
        getattr(message, "photo", None),
    ):
        size = getattr(payload, "size", None)
        if isinstance(size, bool):
            continue
        if isinstance(size, int) and size >= 0:
            return size
    return None
