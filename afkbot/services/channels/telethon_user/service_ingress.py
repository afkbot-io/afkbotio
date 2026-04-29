"""Ingress, delivery, and retry helpers for the Telethon user-channel runtime."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    build_routing_context_overrides,
)
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channels.context_overrides import build_channel_tool_profile_context_overrides
from afkbot.services.channels.contracts import ChannelDeliveryTarget, ChannelOutboundMessage
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError
from afkbot.services.channels.ingress_coalescer import (
    ChannelIngressBatch,
    ChannelIngressEvent,
    build_ingress_batch_context_overrides,
    render_channel_ingress_batch_message,
)
from afkbot.services.channels.ingress_journal import get_channel_ingress_journal_service
from afkbot.services.channels.ingress_persistence import get_channel_ingress_pending_service
from afkbot.services.channels.reply_humanization import simulate_telethon_reply_humanization
from afkbot.services.channels.reply_policy import should_suppress_channel_reply
from afkbot.services.channels.media_ingest import resolve_channel_outbound_media_path
from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.services.telegram_text import split_telegram_text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from afkbot.services.channel_routing.runtime_target import RuntimeTarget
    from afkbot.services.channels.telethon_user.service import TelethonUserService, _QueuedInboundEvent


async def worker_loop(service: TelethonUserService) -> None:
    """Process queued inbound events through batching or immediate delivery."""

    journal = get_channel_ingress_journal_service(service._settings)
    while not service._stop_event.is_set():
        item = await service._queue.get()
        claimed = False
        try:
            claimed = await journal.try_claim(
                endpoint_id=service._endpoint.endpoint_id,
                transport=service._endpoint.transport,
                event_key=item.event_key,
            )
            if not claimed:
                continue
            if service._endpoint.ingress_batch.enabled:
                await service._ingress_coalescer.enqueue(service._to_ingress_event(item))
            else:
                await service._handle_inbound_event(item)
        except asyncio.CancelledError:
            if claimed:
                await journal.release_claim(
                    endpoint_id=service._endpoint.endpoint_id,
                    event_key=item.event_key,
                )
            raise
        except Exception:
            if claimed:
                await journal.release_claim(
                    endpoint_id=service._endpoint.endpoint_id,
                    event_key=item.event_key,
                )
            service.logger.exception(
                "telethon_user_event_failed endpoint_id=%s event_key=%s",
                service._endpoint.endpoint_id,
                item.event_key,
            )
        finally:
            service._queue.task_done()


async def handle_inbound_event(service: TelethonUserService, item: _QueuedInboundEvent) -> None:
    """Wrap one inbound queue item into a single-event batch and flush it."""

    await service._flush_inbound_batch(
        ChannelIngressBatch(
            endpoint_id=service._endpoint.endpoint_id,
            transport=service._endpoint.transport,
            account_id=service._endpoint.account_id,
            peer_id=item.chat_id,
            thread_id=item.thread_id,
            user_id=item.user_id,
            chat_kind=item.chat_kind,
            events=(service._to_ingress_event(item),),
        )
    )


async def flush_inbound_batch(
    service: TelethonUserService,
    *,
    batch: ChannelIngressBatch,
    resolve_runtime_target_fn: Callable[..., Awaitable[RuntimeTarget]],
) -> None:
    """Run one Telethon ingress batch through routing, AgentLoop, and optional reply delivery."""

    selectors = RoutingSelectors(
        transport=service._endpoint.transport,
        account_id=service._endpoint.account_id,
        peer_id=batch.peer_id,
        thread_id=batch.thread_id,
        user_id=batch.user_id,
    )
    try:
        target = await resolve_runtime_target_fn(
            settings=service._settings,
            explicit_profile_id=None,
            explicit_session_id=None,
            resolve_binding=True,
            selectors=selectors,
            default_profile_id=service._endpoint.profile_id,
            default_session_id=f"telegram_user:{batch.peer_id}",
        )
    except ChannelBindingServiceError as exc:
        if exc.error_code != "channel_binding_no_match":
            raise
        return
    context_overrides = merge_turn_context_overrides(
        build_routing_context_overrides(target=target, selectors=selectors),
        build_ingress_batch_context_overrides(batch),
        build_channel_tool_profile_context_overrides(service._endpoint.tool_profile),
    )
    turn_result = await service._run_chat_turn(
        message=render_channel_ingress_batch_message(batch),
        profile_id=target.profile_id,
        session_id=target.session_id,
        client_msg_id=service._build_batch_client_msg_id(batch),
        context_overrides=context_overrides,
    )
    if service._endpoint.reply_mode != "same_chat":
        return
    if turn_result.envelope.action != "finalize":
        return
    if should_suppress_channel_reply(turn_result.envelope):
        service.logger.warning(
            "telethon_user_suppressed_llm_error endpoint_id=%s run_id=%s",
            service._endpoint.endpoint_id,
            turn_result.run_id,
        )
        return
    response_text = turn_result.envelope.message.strip()
    if not response_text:
        return
    entity = resolve_outbound_entity(batch.peer_id)
    await simulate_telethon_reply_humanization(
        client=service._client,
        entity=entity,
        text=response_text,
        config=service._endpoint.reply_humanization,
        mark_read_before_reply=service._endpoint.mark_read_before_reply,
        last_message_id=parse_last_batch_message_id(batch),
    )
    await service._channel_delivery_service.deliver_text(
        profile_id=turn_result.profile_id,
        session_id=turn_result.session_id,
        run_id=turn_result.run_id,
        target=ChannelDeliveryTarget(
            transport=service._endpoint.transport,
            account_id=service._endpoint.account_id,
            peer_id=batch.peer_id,
            thread_id=batch.thread_id,
            user_id=batch.user_id,
        ),
        text=response_text,
        credential_profile_key=service._endpoint.credential_profile_key,
    )


async def handle_ingress_batch_error(
    service: TelethonUserService,
    *,
    batch: ChannelIngressBatch,
    exc: Exception,
) -> None:
    """Release journal claims and translate transient delivery errors into deferred retries."""

    journal = get_channel_ingress_journal_service(service._settings)
    for item in batch.events:
        await journal.release_claim(
            endpoint_id=service._endpoint.endpoint_id,
            event_key=item.event_key,
        )
    retry_after_sec = extract_delivery_retry_after_sec(exc)
    if retry_after_sec is not None:
        await service._schedule_pending_ingress_retry(retry_after_sec=retry_after_sec)
        service.logger.warning(
            "telethon_user_batch_deferred endpoint_id=%s peer_id=%s user_id=%s "
            "batch_size=%s retry_after_sec=%s error_code=%s",
            batch.endpoint_id,
            batch.peer_id,
            batch.user_id,
            len(batch.events),
            retry_after_sec,
            getattr(exc, "error_code", exc.__class__.__name__),
        )
        return
    service.logger.exception(
        "telethon_user_batch_failed endpoint_id=%s peer_id=%s user_id=%s batch_size=%s exc=%s",
        batch.endpoint_id,
        batch.peer_id,
        batch.user_id,
        len(batch.events),
        f"{exc.__class__.__name__}: {exc}",
    )


async def persist_pending_ingress_event(service: TelethonUserService, event: ChannelIngressEvent) -> bool:
    """Persist one ingress event to pending storage."""

    return await get_channel_ingress_pending_service(service._settings).record_pending(event=event)


async def release_pending_ingress_batch(service: TelethonUserService, batch: ChannelIngressBatch) -> None:
    """Release one persisted ingress batch from pending storage."""

    await get_channel_ingress_pending_service(service._settings).release_batch(batch=batch)


async def restore_pending_ingress_events(service: TelethonUserService) -> None:
    """Restore persisted ingress events once per runtime start."""

    if service._pending_restored:
        return
    await flush_persisted_pending_ingress(service)
    service._pending_restored = True


async def flush_persisted_pending_ingress(service: TelethonUserService) -> None:
    """Push all persisted pending ingress events back into the coalescer."""

    events = await get_channel_ingress_pending_service(service._settings).list_pending(
        endpoint_id=service._endpoint.endpoint_id
    )
    if events:
        await service._ingress_coalescer.restore_pending(tuple(events))


async def spill_overflow_event(service: TelethonUserService, item: _QueuedInboundEvent) -> None:
    """Persist one overflowed queue item for deferred retry instead of dropping it."""

    ingress_event = service._to_ingress_event(item)
    accepted = await get_channel_ingress_pending_service(service._settings).record_pending(
        event=ingress_event
    )
    if accepted:
        await service._schedule_pending_ingress_retry(retry_after_sec=1)
    service.logger.warning(
        "telethon_user_queue_overflow_spilled endpoint_id=%s event_key=%s persisted=%s",
        service._endpoint.endpoint_id,
        item.event_key,
        accepted,
    )


async def schedule_pending_ingress_retry(service: TelethonUserService, *, retry_after_sec: int) -> None:
    """Schedule a deferred flush for persisted pending ingress events."""

    delay_sec = max(1, int(retry_after_sec))
    deadline = datetime.now(UTC) + timedelta(seconds=delay_sec)
    task_to_cancel: asyncio.Task[None] | None = None
    async with service._ingress_retry_lock:
        effective_deadline = deadline
        if (
            service._ingress_retry_deadline is not None
            and service._ingress_retry_deadline > effective_deadline
        ):
            effective_deadline = service._ingress_retry_deadline
        if (
            service._ingress_retry_task is not None
            and not service._ingress_retry_task.done()
            and service._ingress_retry_deadline == effective_deadline
        ):
            return
        service._ingress_retry_deadline = effective_deadline
        if service._ingress_retry_task is not None and not service._ingress_retry_task.done():
            task_to_cancel = service._ingress_retry_task
        service._ingress_retry_task = asyncio.create_task(
            service._retry_pending_ingress_after_deadline(deadline=effective_deadline),
            name=f"telethon-user-ingress-retry:{service._endpoint.endpoint_id}",
        )
    if task_to_cancel is not None:
        task_to_cancel.cancel()
        try:
            await task_to_cancel
        except asyncio.CancelledError:
            pass


async def retry_pending_ingress_after_deadline(
    service: TelethonUserService,
    *,
    deadline: datetime,
) -> None:
    """Sleep until the retry deadline and then restore persisted pending ingress events."""

    current_task = asyncio.current_task()
    delay_sec = max((deadline - datetime.now(UTC)).total_seconds(), 0.0)
    try:
        if delay_sec > 0:
            try:
                await asyncio.wait_for(service._stop_event.wait(), timeout=delay_sec)
                return
            except asyncio.TimeoutError:
                pass
        if service._stop_event.is_set():
            return
        await flush_persisted_pending_ingress(service)
    except asyncio.CancelledError:
        raise
    except Exception:
        service.logger.exception(
            "telethon_user_ingress_retry_failed endpoint_id=%s",
            service._endpoint.endpoint_id,
        )
    finally:
        async with service._ingress_retry_lock:
            if service._ingress_retry_task is current_task:
                service._ingress_retry_task = None
                if service._ingress_retry_deadline == deadline:
                    service._ingress_retry_deadline = None


def to_ingress_event(service: TelethonUserService, item: _QueuedInboundEvent) -> ChannelIngressEvent:
    """Translate one queued item into a normalized ingress event."""

    observed_at = getattr(item, "observed_at", None)
    if not isinstance(observed_at, str) or not observed_at.strip():
        observed_at = datetime.now(UTC).isoformat()
    return ChannelIngressEvent(
        endpoint_id=service._endpoint.endpoint_id,
        transport=service._endpoint.transport,
        account_id=service._endpoint.account_id,
        peer_id=item.chat_id,
        thread_id=item.thread_id,
        user_id=item.user_id,
        event_key=item.event_key,
        message_id=str(item.message_id),
        text=item.text,
        observed_at=observed_at,
        chat_kind=item.chat_kind,
    )


def build_batch_client_msg_id(batch: ChannelIngressBatch) -> str:
    """Build a deterministic idempotency key for one Telethon ingress batch."""

    if len(batch.events) == 1:
        return f"telethon:{batch.account_id}:{batch.peer_id}:{batch.events[0].message_id}"
    first_id = batch.events[0].message_id
    last_id = batch.events[-1].message_id
    return (
        f"telethon-batch:{batch.account_id}:{batch.peer_id}:{batch.thread_id or '-'}:"
        f"{batch.user_id or '-'}:{first_id}:{last_id}:{len(batch.events)}"
    )


async def send_text_via_live_client(
    service: TelethonUserService,
    *,
    target: Any,
    text: str | ChannelOutboundMessage,
) -> dict[str, object]:
    """Send one outbound message through the live Telethon client with structured errors."""

    client = service._client
    if client is None:
        raise TelethonUserServiceError(
            error_code="telethon_sender_not_connected",
            reason="Telethon sender is not connected.",
            metadata=target.to_payload(),
        )
    if target.thread_id is not None:
        raise TelethonUserServiceError(
            error_code="telethon_thread_not_supported",
            reason="Telethon user transport does not support outbound thread_id yet.",
            metadata=target.to_payload(),
        )
    try:
        entity = resolve_outbound_entity(target.peer_id)
    except Exception as exc:
        raise TelethonUserServiceError(
            error_code="telethon_invalid_peer_id",
            reason=f"Invalid Telethon peer_id: {target.peer_id}",
            metadata=target.to_payload(),
        ) from exc
    if isinstance(text, ChannelOutboundMessage):
        message = text
    else:
        message = ChannelOutboundMessage(text=text)
    try:
        last_result: object | None = None
        buttons = _build_telethon_buttons(message.reply_markup)
        for attachment in message.attachments:
            source = await _resolve_telethon_attachment_source(
                service=service,
                target=target,
                source=attachment.source,
            )
            send_file = getattr(client, "send_file", None)
            if not callable(send_file):
                raise TelethonUserServiceError(
                    error_code="telethon_media_not_supported",
                    reason="Connected Telethon client does not support file delivery.",
                    metadata=target.to_payload(),
                )
            kwargs: dict[str, object] = {}
            if attachment.caption:
                kwargs["caption"] = attachment.caption
            if attachment.parse_mode:
                kwargs["parse_mode"] = attachment.parse_mode
            if buttons is not None:
                kwargs["buttons"] = buttons
            last_result = await send_file(entity, source, **kwargs)
        if message.text:
            chunks = split_telegram_text(message.text)
            for index, chunk in enumerate(chunks):
                kwargs = {}
                if message.parse_mode:
                    kwargs["parse_mode"] = message.parse_mode
                if buttons is not None and index == len(chunks) - 1:
                    kwargs["buttons"] = buttons
                last_result = await client.send_message(entity, chunk, **kwargs)
        if last_result is None:
            raise TelethonUserServiceError(
                error_code="telethon_message_empty",
                reason="Telethon outbound message is empty.",
                metadata=target.to_payload(),
            )
    except Exception as exc:
        retry_after_sec = extract_flood_wait_retry_after_sec(exc)
        if retry_after_sec is not None:
            raise TelethonUserServiceError(
                error_code="telethon_flood_wait",
                reason=(
                    "Telegram rate-limited the user account for outbound sends. "
                    f"Retry after {retry_after_sec} seconds."
                ),
                metadata={
                    **target.to_payload(),
                    "retry_after_sec": retry_after_sec,
                },
            ) from exc
        if isinstance(exc, TelethonUserServiceError):
            raise
        raise
    message_id = getattr(last_result, "id", None)
    payload: dict[str, object] = {"peer_id": str(target.peer_id), "text": message.text}
    if isinstance(message_id, int):
        payload["message_id"] = message_id
    return payload


async def _resolve_telethon_attachment_source(
    *,
    service: TelethonUserService,
    target: Any,
    source: str,
) -> str:
    try:
        local_path = await resolve_channel_outbound_media_path(
            settings=service._settings,
            profile_id=service._endpoint.profile_id,
            raw_value=source,
            label="Telethon media",
        )
    except ValueError as exc:
        raise TelethonUserServiceError(
            error_code="telethon_media_path_invalid",
            reason=str(exc),
            metadata=target.to_payload(),
        ) from exc
    if local_path is None:
        return source
    size_bytes = local_path.stat().st_size
    max_bytes = service._settings.channel_media_upload_max_bytes
    if size_bytes > max_bytes:
        raise TelethonUserServiceError(
            error_code="telethon_media_too_large",
            reason=f"Telethon media file exceeds max upload size: {size_bytes} > {max_bytes}",
            metadata=target.to_payload(),
        )
    return str(local_path)


def _build_telethon_buttons(reply_markup: dict[str, object] | None) -> object | None:
    """Best-effort conversion from Bot API reply_markup to Telethon buttons."""

    if not reply_markup:
        return None
    try:
        from telethon import Button  # type: ignore[import-untyped]
    except Exception:
        return reply_markup
    inline_keyboard = reply_markup.get("inline_keyboard")
    if isinstance(inline_keyboard, list):
        rows: list[list[object]] = []
        for row in inline_keyboard:
            if not isinstance(row, list):
                continue
            rendered_row: list[object] = []
            for button in row:
                rendered = _build_telethon_inline_button(Button, button)
                if rendered is not None:
                    rendered_row.append(rendered)
            if rendered_row:
                rows.append(rendered_row)
        return rows or None
    keyboard = reply_markup.get("keyboard")
    if isinstance(keyboard, list):
        rows = []
        for row in keyboard:
            if not isinstance(row, list):
                continue
            rendered_row = []
            for button in row:
                text = button if isinstance(button, str) else button.get("text") if isinstance(button, dict) else None
                if isinstance(text, str) and text.strip():
                    rendered_row.append(Button.text(text.strip()))
            if rendered_row:
                rows.append(rendered_row)
        return rows or None
    return reply_markup


def _build_telethon_inline_button(button_factory: Any, payload: object) -> object | None:
    if not isinstance(payload, dict):
        return None
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    label = text.strip()
    callback_data = payload.get("callback_data")
    if isinstance(callback_data, str):
        return cast(object, button_factory.inline(label, data=callback_data.encode("utf-8")))
    url = payload.get("url")
    if isinstance(url, str) and url.strip():
        return cast(object, button_factory.url(label, url.strip()))
    switch_current = payload.get("switch_inline_query_current_chat")
    if isinstance(switch_current, str):
        return cast(object, button_factory.switch_inline(label, query=switch_current, same_peer=True))
    switch_query = payload.get("switch_inline_query")
    if isinstance(switch_query, str):
        return cast(object, button_factory.switch_inline(label, query=switch_query, same_peer=False))
    return cast(object, button_factory.inline(label, data=label.encode("utf-8")))


def extract_flood_wait_retry_after_sec(exc: Exception) -> int | None:
    """Extract a Telethon FloodWait retry horizon from a raw exception."""

    class_name = exc.__class__.__name__
    if class_name != "FloodWaitError" and not any(
        hasattr(exc, attr_name) for attr_name in ("seconds", "value", "x")
    ):
        return None
    for attr_name in ("seconds", "value", "x"):
        value = getattr(exc, attr_name, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(1, value)
        if isinstance(value, float):
            return max(1, int(value))
    return None


def extract_delivery_retry_after_sec(exc: Exception) -> int | None:
    """Extract retry_after_sec from one structured delivery failure."""

    if not isinstance(exc, ChannelDeliveryServiceError):
        return None
    raw_value = exc.metadata.get("retry_after_sec")
    if isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, int):
        return max(1, raw_value)
    if isinstance(raw_value, float):
        return max(1, int(raw_value))
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        if normalized.isdigit():
            return max(1, int(normalized))
    return None


def resolve_outbound_entity(peer_id: str | None) -> object | None:
    """Normalize one outbound peer id into Telethon entity input."""

    if peer_id is None:
        return None
    if peer_id.strip().lower() in {"me", "self", "saved_messages"}:
        return "me"
    try:
        return int(peer_id)
    except ValueError:
        return peer_id


def parse_last_batch_message_id(batch: ChannelIngressBatch) -> int | None:
    """Return the last message id in a batch when it is parseable."""

    try:
        return int(batch.events[-1].message_id)
    except (IndexError, TypeError, ValueError):
        return None
