"""Batching, offset, and delivery helpers for Telegram Bot API polling."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    build_routing_context_overrides,
)
from afkbot.services.channel_routing.service import ChannelBindingServiceError
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
from afkbot.services.channels.reply_humanization import simulate_telegram_bot_reply_humanization
from afkbot.services.channels.reply_policy import should_suppress_channel_reply
from afkbot.services.channels.telegram_polling_support import (
    TelegramInboundMessage,
    extract_inbound_message,
    load_next_update_offset,
    persist_next_update_offset,
)
from afkbot.services.channels.telegram_timeouts import is_telegram_action_timeout_reason


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
                text=inbound.text,
                observed_at=datetime.now(UTC).isoformat(),
                chat_kind=inbound.chat_type,
            )
        )

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
