"""Batching, offset, and delivery helpers for Telegram Bot API polling."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    build_routing_context_overrides,
)
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channels.access_policy import is_channel_message_allowed
from afkbot.services.channels.context_overrides import build_channel_tool_profile_context_overrides
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError
from afkbot.services.channels.ingress_coalescer import (
    ChannelIngressBatch,
    ChannelIngressEvent,
    build_ingress_batch_context_overrides,
    render_channel_ingress_batch_message,
)
from afkbot.services.channels.ingress_persistence import get_channel_ingress_pending_service
from afkbot.services.channels.media_ingest import (
    build_channel_attachment_dir,
    build_text_preview,
    relative_to_profile_workspace,
    safe_filename,
)
from afkbot.services.channels.reply_humanization import simulate_telegram_bot_reply_humanization
from afkbot.services.channels.reply_policy import should_suppress_channel_reply
from afkbot.services.channels.telegram_polling_support import (
    TelegramInboundAttachment,
    TelegramInboundMessage,
    extract_inbound_message,
    load_next_update_offset,
    persist_next_update_offset,
)
from afkbot.services.channels.telegram_timeouts import is_telegram_action_timeout_reason
from afkbot.services.tools.workspace import resolve_tool_workspace_base_dir


_LOGGER = logging.getLogger(__name__)


class TelegramPollingRuntimeMixin:
    """Shared runtime helpers for update processing, batching, and retries."""

    async def _process_updates(
        self: Any,
        updates: list[dict[str, object]],
    ) -> None:
        """Process one Telegram update batch while tracking idempotent offsets."""

        for update in updates:
            update_id = update.get("update_id")
            if not isinstance(update_id, int):
                continue
            await self._register_pending_update(update_id)
            try:
                inbound = self._extract_inbound_message(update)
            except Exception:
                _LOGGER.exception("telegram_polling_update_failed update_id=%s", update_id)
                await self._advance_fetch_offset(update_id)
                await self._mark_update_processed(update_id)
                continue
            if inbound is not None:
                try:
                    await self._handle_inbound_message(inbound)
                except Exception:
                    if not self._endpoint.ingress_batch.enabled:
                        await get_channel_ingress_pending_service(self._settings).release_event(
                            endpoint_id=self._endpoint.endpoint_id,
                            event_key=str(inbound.update_id),
                        )
                    raise
                await self._advance_fetch_offset(update_id)
                if not self._endpoint.ingress_batch.enabled:
                    await self._mark_update_processed(update_id)
                continue
            await self._advance_fetch_offset(update_id)
            await self._mark_update_processed(update_id)

    def _extract_inbound_message(
        self: Any,
        update: dict[str, object],
    ) -> TelegramInboundMessage | None:
        """Normalize one raw Telegram update into model-facing inbound text."""

        return extract_inbound_message(
            update=update,
            identity=self._bot_identity,
            group_trigger_mode=self._group_trigger_mode,
        )

    async def _handle_inbound_message(
        self: Any,
        inbound: TelegramInboundMessage,
    ) -> None:
        """Enqueue one normalized inbound Telegram message into the ingress coalescer."""

        if not is_channel_message_allowed(
            policy=self._endpoint.access_policy,
            chat_kind=inbound.chat_type,
            peer_id=inbound.chat_id,
            user_id=inbound.user_id,
        ):
            _LOGGER.warning(
                "telegram_polling_access_denied account_id=%s peer_id=%s user_id=%s chat_type=%s",
                self._account_id,
                inbound.chat_id,
                inbound.user_id,
                inbound.chat_type,
            )
            return
        if inbound.callback_query_id is not None:
            await self._answer_callback_query(inbound.callback_query_id)
        text = await self._enrich_inbound_text_with_media(inbound)
        await self._ingress_coalescer.enqueue(
            ChannelIngressEvent(
                endpoint_id=self._endpoint.endpoint_id,
                transport="telegram",
                account_id=self._account_id,
                peer_id=inbound.chat_id,
                thread_id=inbound.thread_id,
                user_id=inbound.user_id,
                event_key=str(inbound.update_id),
                message_id=str(inbound.update_id),
                source_event_id=str(inbound.update_id),
                text=text,
                observed_at=datetime.now(UTC).isoformat(),
                chat_kind=inbound.chat_type,
            )
        )

    async def _answer_callback_query(
        self: Any,
        callback_query_id: str,
    ) -> None:
        """Acknowledge one Telegram inline-button callback without blocking routing."""

        result = await self._app_runtime.run(
            app="telegram",
            action="answer_callback_query",
            ctx=self._app_context(timeout_sec=min(10, self._settings.tool_timeout_max_sec)),
            params={"callback_query_id": callback_query_id},
        )
        if not result.ok:
            _LOGGER.warning(
                "telegram_callback_ack_failed endpoint_id=%s error_code=%s reason=%s",
                self._endpoint.endpoint_id,
                result.error_code,
                result.reason,
            )

    async def _enrich_inbound_text_with_media(
        self: Any,
        inbound: TelegramInboundMessage,
    ) -> str:
        """Download inbound Bot API media and append workspace paths for the agent."""

        if not inbound.attachments:
            return inbound.text
        destination = build_channel_attachment_dir(
            settings=self._settings,
            profile_id=self._runtime_profile_id,
            transport="telegram",
            endpoint_id=self._endpoint.endpoint_id,
            event_id=str(inbound.update_id),
        )
        destination_dir = relative_to_profile_workspace(
            settings=self._settings,
            profile_id=self._runtime_profile_id,
            path=destination,
        )
        workspace_base = resolve_tool_workspace_base_dir(
            settings=self._settings,
            profile_id=self._runtime_profile_id,
        )
        lines: list[str] = []
        for attachment in inbound.attachments:
            suggested_file_name = _suggest_attachment_filename(attachment)
            result = await self._app_runtime.run(
                app="telegram",
                action="download_file",
                ctx=self._app_context(timeout_sec=min(60, self._settings.tool_timeout_max_sec)),
                params={
                    "file_id": attachment.file_id,
                    "destination_dir": destination_dir,
                    "suggested_file_name": suggested_file_name,
                    "max_bytes": self._settings.channel_media_download_max_bytes,
                },
            )
            if not result.ok:
                lines.append(
                    f"- {attachment.kind}: download failed"
                    f" ({result.error_code or 'unknown_error'})"
                )
                continue
            payload = result.payload
            path_value = payload.get("path")
            if not isinstance(path_value, str) or not path_value.strip():
                lines.append(f"- {attachment.kind}: downloaded, path unavailable")
                continue
            mime_type = _first_text(payload.get("mime_type"), attachment.mime_type)
            size_value = payload.get("size_bytes")
            details = [path_value.strip()]
            if mime_type:
                details.append(mime_type)
            if isinstance(size_value, int) and size_value >= 0:
                details.append(f"{size_value} bytes")
            lines.append(f"- {attachment.kind}: " + ", ".join(details))
            preview = _build_download_preview(
                base_path=workspace_base,
                relative_path=path_value.strip(),
                mime_type=mime_type,
                max_bytes=self._settings.channel_media_text_preview_bytes,
            )
            if preview is not None:
                preview_text, truncated = preview
                suffix = " [truncated]" if truncated else ""
                lines.append(f"  text preview{suffix}:\n{preview_text}")
        if not lines:
            return inbound.text
        return f"{inbound.text}\n\nDownloaded Telegram attachments:\n" + "\n".join(lines)

    async def _flush_inbound_batch(
        self: Any,
        batch: ChannelIngressBatch,
    ) -> None:
        """Flush one coalesced Telegram ingress batch through AgentLoop and delivery."""

        selectors = RoutingSelectors(
            transport="telegram",
            account_id=batch.account_id,
            peer_id=batch.peer_id,
            thread_id=batch.thread_id,
            user_id=batch.user_id,
        )
        try:
            target = await self._resolve_runtime_target(
                selectors=selectors,
                default_session_id=f"telegram:{batch.peer_id}",
            )
        except ChannelBindingServiceError as exc:
            if exc.error_code != "channel_binding_no_match":
                raise
            _LOGGER.warning(
                "telegram_polling_binding_no_match account_id=%s peer_id=%s thread_id=%s user_id=%s",
                batch.account_id,
                batch.peer_id,
                batch.thread_id,
                batch.user_id,
            )
            await self._mark_batch_processed(batch)
            return
        context_overrides = merge_turn_context_overrides(
            build_routing_context_overrides(target=target, selectors=selectors),
            build_ingress_batch_context_overrides(batch),
            build_channel_tool_profile_context_overrides(self._endpoint.tool_profile),
        )
        turn_result = await self._run_chat_turn(
            message=render_channel_ingress_batch_message(batch),
            profile_id=target.profile_id,
            session_id=target.session_id,
            client_msg_id=self._build_batch_client_msg_id(batch),
            context_overrides=context_overrides,
        )
        if turn_result.envelope.action != "finalize":
            await self._mark_batch_processed(batch)
            return
        if should_suppress_channel_reply(turn_result.envelope):
            _LOGGER.warning(
                "telegram_polling_suppressed_llm_error endpoint_id=%s run_id=%s",
                self._endpoint.endpoint_id,
                turn_result.run_id,
            )
            await self._mark_batch_processed(batch)
            return
        response_text = turn_result.envelope.message.strip()
        if not response_text:
            await self._mark_batch_processed(batch)
            return
        await simulate_telegram_bot_reply_humanization(
            settings=self._settings,
            app_runtime=self._app_runtime,
            profile_id=turn_result.profile_id,
            session_id=turn_result.session_id,
            run_id=turn_result.run_id,
            credential_profile_key=self._credential_profile_key,
            chat_id=batch.peer_id,
            thread_id=batch.thread_id,
            text=response_text,
            config=self._endpoint.reply_humanization,
        )
        await self._channel_delivery_service.deliver_text(
            profile_id=turn_result.profile_id,
            session_id=turn_result.session_id,
            run_id=turn_result.run_id,
            target=ChannelDeliveryTarget(
                transport="telegram",
                account_id=batch.account_id,
                peer_id=batch.peer_id,
                thread_id=batch.thread_id,
                user_id=batch.user_id,
            ),
            text=response_text,
            credential_profile_key=self._credential_profile_key,
        )
        self._clear_delivery_retry_state(batch.conversation_key)
        await self._mark_batch_processed(batch)

    async def _handle_ingress_batch_error(
        self: Any,
        batch: ChannelIngressBatch,
        exc: Exception,
    ) -> None:
        """Handle one failed ingress-batch flush, optionally scheduling retry."""

        if self._is_transient_delivery_timeout(exc):
            delay_sec = self._next_delivery_retry_delay_sec(batch.conversation_key)
            self._schedule_delivery_retry(batch=batch, delay_sec=delay_sec)
            _LOGGER.warning(
                "telegram_polling_batch_timeout_retry_scheduled endpoint_id=%s peer_id=%s user_id=%s batch_size=%s delay_sec=%s exc=%s",
                batch.endpoint_id,
                batch.peer_id,
                batch.user_id,
                len(batch.events),
                delay_sec,
                f"{exc.__class__.__name__}: {exc}",
            )
            return
        _LOGGER.exception(
            "telegram_polling_batch_failed endpoint_id=%s peer_id=%s user_id=%s batch_size=%s exc=%s",
            batch.endpoint_id,
            batch.peer_id,
            batch.user_id,
            len(batch.events),
            f"{exc.__class__.__name__}: {exc}",
        )

    async def _persist_pending_ingress_event(
        self: Any,
        event: ChannelIngressEvent,
    ) -> bool:
        """Persist one pending Telegram ingress event for later replay."""

        return await get_channel_ingress_pending_service(self._settings).record_pending(event=event)

    async def _release_pending_ingress_batch(
        self: Any,
        batch: ChannelIngressBatch,
    ) -> None:
        """Release persisted state for one already processed Telegram batch."""

        await get_channel_ingress_pending_service(self._settings).release_batch(batch=batch)

    async def _restore_pending_ingress_events(self: Any) -> None:
        """Restore pending ingress events once per service lifetime before polling."""

        if self._pending_restored:
            return
        events = await get_channel_ingress_pending_service(self._settings).list_pending(
            endpoint_id=self._endpoint.endpoint_id
        )
        if events:
            await self._ingress_coalescer.restore_pending(tuple(events))
        self._pending_restored = True

    async def _load_next_update_offset(self: Any) -> int | None:
        """Load the persisted Telegram polling offset for this endpoint/account."""

        return load_next_update_offset(
            state_path=self._state_path,
            account_id=self._account_id,
        )

    async def _persist_next_update_offset(
        self: Any,
        next_update_offset: int,
    ) -> None:
        """Persist the next Telegram update offset after acknowledged processing."""

        persist_next_update_offset(
            state_path=self._state_path,
            account_id=self._account_id,
            next_update_offset=next_update_offset,
        )

    async def _reset_offset_tracking(
        self: Any,
        next_update_offset: int | None,
    ) -> None:
        """Reset in-memory offset tracking to one known persisted boundary."""

        async with self._offset_lock:
            self._next_update_offset = next_update_offset
            self._persisted_next_update_offset = next_update_offset
            self._pending_update_order.clear()
            self._pending_update_status.clear()

    async def _register_pending_update(
        self: Any,
        update_id: int,
    ) -> None:
        """Register one freshly fetched update as pending acknowledgment."""

        async with self._offset_lock:
            if update_id in self._pending_update_status:
                return
            self._pending_update_order.append(update_id)
            self._pending_update_status[update_id] = False

    async def _advance_fetch_offset(
        self: Any,
        update_id: int,
    ) -> None:
        """Advance the fetch offset optimistically after one update has been examined."""

        candidate = update_id + 1
        async with self._offset_lock:
            if self._next_update_offset is None or candidate > self._next_update_offset:
                self._next_update_offset = candidate

    async def _mark_update_processed(
        self: Any,
        update_id: int,
    ) -> None:
        """Acknowledge one processed update and persist the next safe offset."""

        async with self._offset_lock:
            if update_id not in self._pending_update_status:
                self._pending_update_order.append(update_id)
            self._pending_update_status[update_id] = True
            persist_offset = self._drain_acknowledged_updates_locked()
        if persist_offset is not None:
            await self._persist_next_update_offset(persist_offset)

    async def _mark_batch_processed(
        self: Any,
        batch: ChannelIngressBatch,
    ) -> None:
        """Acknowledge every source update represented by one ingress batch."""

        for item in batch.events:
            source_event_id = item.source_event_id or item.event_key
            try:
                update_id = int(source_event_id)
            except (TypeError, ValueError):
                continue
            await self._mark_update_processed(update_id)

    def _drain_acknowledged_updates_locked(self: Any) -> int | None:
        """Drain acknowledged updates from the in-memory queue while holding the offset lock."""

        persist_offset: int | None = None
        while self._pending_update_order:
            update_id = self._pending_update_order[0]
            if not self._pending_update_status.get(update_id, False):
                break
            self._pending_update_order.popleft()
            self._pending_update_status.pop(update_id, None)
            persist_offset = update_id + 1
        if persist_offset is None or persist_offset == self._persisted_next_update_offset:
            return None
        self._persisted_next_update_offset = persist_offset
        return persist_offset

    def _is_transient_delivery_timeout(
        self: Any,
        exc: Exception,
    ) -> bool:
        """Return whether one delivery error is a retryable Telegram timeout."""

        if not isinstance(exc, ChannelDeliveryServiceError):
            return False
        return is_telegram_action_timeout_reason(exc.reason)

    def _next_delivery_retry_delay_sec(
        self: Any,
        conversation_key: str,
    ) -> float:
        """Return bounded exponential backoff for one conversation retry."""

        current_attempt = self._delivery_retry_attempts.get(conversation_key, 0) + 1
        self._delivery_retry_attempts[conversation_key] = current_attempt
        return min(60.0, float(5 * (2 ** (current_attempt - 1))))

    def _schedule_delivery_retry(
        self: Any,
        *,
        batch: ChannelIngressBatch,
        delay_sec: float,
    ) -> None:
        """Schedule one retry for a failed Telegram batch flush."""

        key = batch.conversation_key
        existing = self._delivery_retry_tasks.get(key)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(
            self._retry_delivery_batch_after_delay(batch=batch, delay_sec=delay_sec),
            name=f"telegram-batch-retry:{batch.endpoint_id}:{batch.peer_id}",
        )
        self._delivery_retry_tasks[key] = task
        task.add_done_callback(lambda _: self._delivery_retry_tasks.pop(key, None))

    async def _retry_delivery_batch_after_delay(
        self: Any,
        *,
        batch: ChannelIngressBatch,
        delay_sec: float,
    ) -> None:
        """Restore one failed batch through the pending-ingress path after delay."""

        try:
            await asyncio.sleep(max(delay_sec, 0.0))
            await self._ingress_coalescer.restore_pending(batch.events)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "telegram_delivery_retry_restore_failed endpoint_id=%s peer_id=%s conversation_key=%s",
                batch.endpoint_id,
                batch.peer_id,
                batch.conversation_key,
            )

    def _clear_delivery_retry_state(
        self: Any,
        conversation_key: str,
    ) -> None:
        """Drop retry bookkeeping after one conversation flush succeeds."""

        self._delivery_retry_attempts.pop(conversation_key, None)

    @staticmethod
    def _build_batch_client_msg_id(batch: ChannelIngressBatch) -> str:
        """Build one deterministic client message id for a Telegram ingress batch."""

        if len(batch.events) == 1:
            return f"telegram:{batch.account_id}:{batch.events[0].message_id}"
        first_id = batch.events[0].message_id
        last_id = batch.events[-1].message_id
        return (
            f"telegram-batch:{batch.account_id}:{batch.peer_id}:{batch.thread_id or '-'}:"
            f"{batch.user_id or '-'}:{first_id}:{last_id}:{len(batch.events)}"
        )


def _suggest_attachment_filename(attachment: TelegramInboundAttachment) -> str:
    """Build a stable local filename for one Telegram file attachment."""

    if attachment.file_name:
        return safe_filename(attachment.file_name)
    ext = _extension_for_attachment(attachment)
    unique = attachment.file_unique_id or attachment.file_id
    suffix = safe_filename(unique, fallback=attachment.kind)
    if attachment.kind == "voice":
        return f"voice{ext}"
    if suffix == attachment.kind:
        return f"{attachment.kind}{ext}"
    return f"{attachment.kind}_{suffix}{ext}"


def _extension_for_attachment(attachment: TelegramInboundAttachment) -> str:
    if attachment.kind == "photo":
        return ".jpg"
    if attachment.kind == "voice" and attachment.mime_type == "audio/ogg":
        return ".ogg"
    if attachment.kind == "sticker":
        if attachment.is_video:
            return ".webm"
        if attachment.is_animated:
            return ".tgs"
        return ".webp"
    if attachment.mime_type:
        guessed = mimetypes.guess_extension(attachment.mime_type)
        if guessed:
            return guessed
    return ".bin"


def _first_text(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_download_preview(
    *,
    base_path: Path,
    relative_path: str,
    mime_type: str | None,
    max_bytes: int,
) -> tuple[str, bool] | None:
    path = Path(relative_path)
    candidate = path if path.is_absolute() else base_path / path
    return build_text_preview(path=candidate, mime_type=mime_type, max_bytes=max_bytes)
