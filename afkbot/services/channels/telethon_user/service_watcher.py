"""Watcher-side helpers for the Telethon user-channel runtime."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    RuntimeTarget,
    build_routing_context_overrides,
)
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channels.context_overrides import build_channel_tool_profile_context_overrides
from afkbot.services.channels.ingress_journal import get_channel_ingress_journal_service
from afkbot.services.channels.reply_policy import should_suppress_channel_reply
from afkbot.services.channels.telethon_user.watcher import (
    TelethonWatchedDialog,
    TelethonWatchedEvent,
    build_watcher_context_overrides,
    clip_watched_text,
    is_no_digest_response,
    render_watcher_batch_message,
    resolve_watcher_delivery_target,
    select_watched_dialog,
    watcher_requires_live_sender,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from afkbot.services.channels.telethon_user.client import TelethonClientLike
    from afkbot.services.channels.telethon_user.service import TelethonUserService


def needs_live_sender_registration(service: TelethonUserService) -> bool:
    """Return whether this runtime configuration needs a live Telethon sender."""

    if service._endpoint.reply_mode == "same_chat":
        return True
    if not service._endpoint.watcher.enabled:
        return False
    return watcher_requires_live_sender(
        account_id=service._endpoint.account_id,
        config=service._endpoint.watcher,
    )


async def watcher_flush_loop(service: TelethonUserService) -> None:
    """Flush watcher buffers on the configured interval until shutdown."""

    while not service._stop_event.is_set():
        try:
            await asyncio.wait_for(
                service._stop_event.wait(),
                timeout=service._endpoint.watcher.batch_interval_sec,
            )
            return
        except asyncio.TimeoutError:
            pass
        try:
            await service._flush_watcher_batch()
        except asyncio.CancelledError:
            raise
        except Exception:
            service.logger.exception(
                "telethon_user_watcher_flush_failed endpoint_id=%s",
                service._endpoint.endpoint_id,
            )


async def watcher_refresh_loop(service: TelethonUserService) -> None:
    """Refresh the watched-dialog snapshot on the configured interval until shutdown."""

    while not service._stop_event.is_set():
        try:
            await asyncio.wait_for(
                service._stop_event.wait(),
                timeout=service._endpoint.watcher.dialog_refresh_interval_sec,
            )
            return
        except asyncio.TimeoutError:
            pass
        try:
            await service._refresh_watched_dialogs()
        except asyncio.CancelledError:
            raise
        except Exception:
            service.logger.exception(
                "telethon_user_watcher_refresh_failed endpoint_id=%s",
                service._endpoint.endpoint_id,
            )


async def refresh_watched_dialogs(
    service: TelethonUserService,
    *,
    client: TelethonClientLike | None = None,
) -> None:
    """Rebuild the watched-dialog snapshot from the live Telethon client."""

    live_client = client or service._client
    if live_client is None:
        return
    get_dialogs = getattr(live_client, "get_dialogs", None)
    if not callable(get_dialogs):
        raise service.error_cls(
            error_code="telethon_watcher_dialog_listing_failed",
            reason="Telethon client does not support get_dialogs().",
        )
    dialogs = await get_dialogs(limit=None)
    snapshot: dict[str, TelethonWatchedDialog] = {}
    now = datetime.now(UTC)
    for dialog in dialogs:
        watched = select_watched_dialog(
            dialog=dialog,
            config=service._endpoint.watcher,
            now=now,
        )
        if watched is None:
            continue
        snapshot[watched.chat_id] = watched
    async with service._watcher_lock:
        service._watched_dialogs = snapshot


async def normalize_watched_event(
    service: TelethonUserService,
    *,
    event: object,
) -> TelethonWatchedEvent | None:
    """Normalize one incoming Telethon event for watcher batching."""

    if not service._endpoint.watcher.enabled:
        return None
    if bool(getattr(event, "out", False)):
        return None
    message = getattr(event, "message", None)
    message_id = getattr(message, "id", None)
    if not isinstance(message_id, int):
        return None
    text = service.build_inbound_text(event=event).strip()
    if not text:
        return None
    chat_id_raw = getattr(event, "chat_id", None)
    if chat_id_raw is None:
        return None
    chat_id = str(chat_id_raw)
    async with service._watcher_lock:
        dialog = service._watched_dialogs.get(chat_id)
    if dialog is None:
        return None
    return TelethonWatchedEvent(
        event_key=f"watch:{service._endpoint.account_id}:{chat_id}:{message_id}",
        message_id=message_id,
        chat_id=chat_id,
        chat_kind=dialog.chat_kind,
        chat_title=dialog.title,
        sender_id=str(getattr(event, "sender_id")) if getattr(event, "sender_id", None) is not None else None,
        text=clip_watched_text(
            text=text,
            max_chars=service._endpoint.watcher.max_message_chars,
        ),
        observed_at=datetime.now(UTC).isoformat(),
    )


async def buffer_watched_event(service: TelethonUserService, *, event: TelethonWatchedEvent) -> None:
    """Append one watcher event when it is not already buffered or processed."""

    journal = get_channel_ingress_journal_service(service._settings)
    async with service._watcher_lock:
        if event.event_key in service._watcher_buffer_keys or event.event_key in service._watcher_inflight_keys:
            return
    if await journal.contains(endpoint_id=service._endpoint.endpoint_id, event_key=event.event_key):
        return
    async with service._watcher_lock:
        if event.event_key in service._watcher_buffer_keys or event.event_key in service._watcher_inflight_keys:
            return
        service._watcher_buffer.append(event)
        service._watcher_buffer_keys.add(event.event_key)
        trim_watcher_buffer_locked(service)


async def flush_watcher_batch(
    service: TelethonUserService,
    *,
    resolve_runtime_target_fn: Callable[..., Awaitable[RuntimeTarget]],
) -> None:
    """Run one watcher digest batch through AgentLoop and optional delivery."""

    if not service._endpoint.watcher.enabled:
        return
    batch = await service._pop_watcher_batch()
    if not batch:
        return
    journal = get_channel_ingress_journal_service(service._settings)
    delivery_target = resolve_watcher_delivery_target(
        account_id=service._endpoint.account_id,
        config=service._endpoint.watcher,
    )
    message = render_watcher_batch_message(
        account_id=service._endpoint.account_id,
        events=batch,
    )
    try:
        target = await service._resolve_watcher_runtime_target(
            resolve_runtime_target_fn=resolve_runtime_target_fn,
        )
        context_overrides = merge_turn_context_overrides(
            build_routing_context_overrides(
                target=target,
                selectors=RoutingSelectors(
                    transport=service._endpoint.transport,
                    account_id=service._endpoint.account_id,
                ),
            ),
            build_watcher_context_overrides(
                endpoint_id=service._endpoint.endpoint_id,
                account_id=service._endpoint.account_id,
                events=batch,
                delivery_target=delivery_target,
            ),
            build_channel_tool_profile_context_overrides(service._endpoint.tool_profile),
        )
        turn_result = await service._run_chat_turn(
            message=message,
            profile_id=target.profile_id,
            session_id=target.session_id,
            client_msg_id=service._build_watcher_client_msg_id(batch),
            context_overrides=context_overrides,
        )
        response_text = ""
        if turn_result.envelope.action == "finalize":
            if should_suppress_channel_reply(turn_result.envelope):
                service.logger.warning(
                    "telethon_user_watcher_suppressed_llm_error endpoint_id=%s run_id=%s",
                    service._endpoint.endpoint_id,
                    turn_result.run_id,
                )
            else:
                response_text = turn_result.envelope.message.strip()
        if response_text and not is_no_digest_response(response_text):
            await service._channel_delivery_service.deliver_text(
                profile_id=turn_result.profile_id,
                session_id=turn_result.session_id,
                run_id=turn_result.run_id,
                target=delivery_target,
                text=response_text,
                credential_profile_key=service._endpoint.watcher.delivery_credential_profile_key,
            )
        for item in batch:
            await journal.record_processed(
                endpoint_id=service._endpoint.endpoint_id,
                transport=service._endpoint.transport,
                event_key=item.event_key,
            )
            async with service._watcher_lock:
                service._watcher_inflight_keys.discard(item.event_key)
    except Exception:
        await service._restore_watcher_batch(batch)
        raise


async def resolve_watcher_runtime_target(
    service: TelethonUserService,
    *,
    resolve_runtime_target_fn: Callable[..., Awaitable[RuntimeTarget]],
) -> RuntimeTarget:
    """Resolve runtime target for watcher digest delivery with endpoint fallback."""

    default_session_id = f"telegram_user_watch:{service._endpoint.endpoint_id}"
    selectors = RoutingSelectors(
        transport=service._endpoint.transport,
        account_id=service._endpoint.account_id,
    )
    try:
        resolved = await resolve_runtime_target_fn(
            settings=service._settings,
            explicit_profile_id=None,
            explicit_session_id=None,
            resolve_binding=True,
            selectors=selectors,
            default_profile_id=service._endpoint.profile_id,
            default_session_id=default_session_id,
        )
        return RuntimeTarget(
            profile_id=resolved.profile_id,
            session_id=default_session_id,
            routing=resolved.routing,
        )
    except ChannelBindingServiceError as exc:
        if exc.error_code != "channel_binding_no_match":
            raise
        return RuntimeTarget(
            profile_id=service._endpoint.profile_id,
            session_id=default_session_id,
        )


async def pop_watcher_batch(service: TelethonUserService) -> tuple[TelethonWatchedEvent, ...]:
    """Pop the next watcher batch from the in-memory buffer."""

    async with service._watcher_lock:
        if not service._watcher_buffer:
            return ()
        batch = tuple(service._watcher_buffer[: service._endpoint.watcher.max_batch_size])
        del service._watcher_buffer[: len(batch)]
        for item in batch:
            service._watcher_buffer_keys.discard(item.event_key)
            service._watcher_inflight_keys.add(item.event_key)
        return batch


async def restore_watcher_batch(
    service: TelethonUserService,
    *,
    batch: tuple[TelethonWatchedEvent, ...],
) -> None:
    """Restore one failed watcher batch to the front of the buffer."""

    if not batch:
        return
    async with service._watcher_lock:
        existing = set(service._watcher_buffer_keys)
        for item in batch:
            service._watcher_inflight_keys.discard(item.event_key)
        prefix = [item for item in batch if item.event_key not in existing]
        if not prefix:
            return
        service._watcher_buffer = prefix + service._watcher_buffer
        service._watcher_buffer_keys.update(item.event_key for item in prefix)
        trim_watcher_buffer_locked(service)


def trim_watcher_buffer_locked(service: TelethonUserService) -> None:
    """Trim buffered watcher events down to the configured max size."""

    overflow = len(service._watcher_buffer) - service._endpoint.watcher.max_buffer_size
    if overflow <= 0:
        return
    dropped = service._watcher_buffer[:overflow]
    del service._watcher_buffer[:overflow]
    for item in dropped:
        service._watcher_buffer_keys.discard(item.event_key)
    service.logger.warning(
        "telethon_user_watcher_buffer_overflow endpoint_id=%s dropped=%s",
        service._endpoint.endpoint_id,
        len(dropped),
    )


def build_watcher_client_msg_id(
    service: TelethonUserService,
    *,
    batch: tuple[TelethonWatchedEvent, ...],
) -> str:
    """Build a deterministic idempotency key for one watcher digest batch."""

    digest = hashlib.sha1()
    for item in batch:
        digest.update(item.event_key.encode("utf-8"))
        digest.update(b"\n")
    return f"telethon-watch:{service._endpoint.account_id}:{digest.hexdigest()}"
