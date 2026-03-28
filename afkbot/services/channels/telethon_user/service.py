"""Telethon user-channel runtime facade over lifecycle, ingress, and watcher helpers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from afkbot.services.agent_loop.api_runtime import run_chat_turn
from afkbot.services.channel_routing.runtime_target import RuntimeTarget, resolve_runtime_target
from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.endpoint_service import telethon_user_state_path_for
from afkbot.services.channels.ingress_coalescer import (
    ChannelIngressBatch,
    ChannelIngressCoalescer,
    ChannelIngressEvent,
)
from afkbot.services.channels.service import ChannelDeliveryService
from afkbot.services.channels.telethon_user.client import (
    TelethonClientLike,
    create_telethon_client,
    import_telethon,
)
from afkbot.services.channels.telethon_user.contracts import resolve_telethon_credentials
from afkbot.services.channels.telethon_user.errors import TelethonUserServiceError
from afkbot.services.channels.telethon_user.normalization import (
    TelethonInboundMessage,
    TelethonUserIdentity,
    build_telethon_inbound_text,
)
from afkbot.services.channels.telethon_user.runtime_support import (
    persist_telethon_identity_state,
    resolve_telethon_identity,
    validate_telethon_profile_policy,
)
from afkbot.services.channels.telethon_user.service_events import (
    normalize_event as _normalize_event_impl,
    on_new_message as _on_new_message_impl,
    resolve_reactive_chat_match_text as _resolve_reactive_chat_match_text_impl,
)
from afkbot.services.channels.telethon_user.service_ingress import (
    build_batch_client_msg_id as _build_batch_client_msg_id,
    extract_delivery_retry_after_sec as _extract_delivery_retry_after_sec,
    extract_flood_wait_retry_after_sec as _extract_flood_wait_retry_after_sec,
    flush_inbound_batch as _flush_inbound_batch_impl,
    flush_persisted_pending_ingress as _flush_persisted_pending_ingress_impl,
    handle_inbound_event as _handle_inbound_event_impl,
    handle_ingress_batch_error as _handle_ingress_batch_error_impl,
    parse_last_batch_message_id as _parse_last_batch_message_id,
    persist_pending_ingress_event as _persist_pending_ingress_event_impl,
    release_pending_ingress_batch as _release_pending_ingress_batch_impl,
    resolve_outbound_entity as _resolve_outbound_entity,
    restore_pending_ingress_events as _restore_pending_ingress_events_impl,
    retry_pending_ingress_after_deadline as _retry_pending_ingress_after_deadline_impl,
    schedule_pending_ingress_retry as _schedule_pending_ingress_retry_impl,
    send_text_via_live_client as _send_text_via_live_client_impl,
    spill_overflow_event as _spill_overflow_event_impl,
    to_ingress_event as _to_ingress_event_impl,
    worker_loop as _worker_loop_impl,
)
from afkbot.services.channels.telethon_user.service_lifecycle import (
    probe_identity as _probe_identity_impl,
    reset_state as _reset_state_impl,
    run_until_disconnected as _run_until_disconnected_impl,
    start_runtime as _start_runtime_impl,
    stop_runtime as _stop_runtime_impl,
)
from afkbot.services.channels.telethon_user.service_watcher import (
    build_watcher_client_msg_id as _build_watcher_client_msg_id,
    buffer_watched_event as _buffer_watched_event_impl,
    flush_watcher_batch as _flush_watcher_batch_impl,
    needs_live_sender_registration as _needs_live_sender_registration_impl,
    normalize_watched_event as _normalize_watched_event_impl,
    pop_watcher_batch as _pop_watcher_batch_impl,
    refresh_watched_dialogs as _refresh_watched_dialogs_impl,
    resolve_watcher_runtime_target as _resolve_watcher_runtime_target_impl,
    restore_watcher_batch as _restore_watcher_batch_impl,
    trim_watcher_buffer_locked as _trim_watcher_buffer_locked_impl,
    watcher_flush_loop as _watcher_flush_loop_impl,
    watcher_refresh_loop as _watcher_refresh_loop_impl,
)
from afkbot.services.channels.telethon_user.watcher import TelethonWatchedDialog, TelethonWatchedEvent
from afkbot.settings import Settings

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _QueuedInboundEvent:
    """Normalized inbound event queued for background ingress processing."""

    event_key: str
    message_id: int
    chat_id: str
    chat_kind: str
    user_id: str | None
    thread_id: str | None
    text: str
    observed_at: str
    is_self_command: bool


class TelethonUserService:
    """Receive Telegram user-account messages via Telethon and route them into AgentLoop."""

    logger = _LOGGER
    error_cls = TelethonUserServiceError
    queued_event_cls = _QueuedInboundEvent
    queue_full_error = asyncio.QueueFull
    build_inbound_text = staticmethod(build_telethon_inbound_text)
    resolve_identity = staticmethod(resolve_telethon_identity)

    def __init__(
        self,
        settings: Settings,
        *,
        endpoint: TelethonUserEndpointConfig,
        state_path: Path | None = None,
        channel_delivery_service: ChannelDeliveryService | None = None,
        run_chat_turn_fn: Any = run_chat_turn,
        client_factory: Any = create_telethon_client,
    ) -> None:
        self._settings = settings
        self._endpoint = TelethonUserEndpointConfig.model_validate(endpoint.model_dump())
        self._state_path = state_path or telethon_user_state_path_for(
            settings,
            endpoint_id=self._endpoint.endpoint_id,
        )
        self._channel_delivery_service = channel_delivery_service or ChannelDeliveryService(settings)
        self._run_chat_turn = run_chat_turn_fn
        self._client_factory = client_factory
        self._client: TelethonClientLike | None = None
        self._identity: TelethonUserIdentity | None = None
        self._queue: asyncio.Queue[_QueuedInboundEvent] = asyncio.Queue(maxsize=settings.runtime_queue_max_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._watcher_flush_task: asyncio.Task[None] | None = None
        self._watcher_refresh_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._event_builder: object | None = None
        self._watcher_lock = asyncio.Lock()
        self._watched_dialogs: dict[str, TelethonWatchedDialog] = {}
        self._watcher_buffer: list[TelethonWatchedEvent] = []
        self._watcher_buffer_keys: set[str] = set()
        self._watcher_inflight_keys: set[str] = set()
        self._ingress_coalescer = ChannelIngressCoalescer(
            config=self._endpoint.ingress_batch,
            on_flush=self._flush_inbound_batch,
            on_flush_error=self._handle_ingress_batch_error,
            persist_event=self._persist_pending_ingress_event,
            release_batch=self._release_pending_ingress_batch,
        )
        self._pending_restored = False
        self._ingress_retry_task: asyncio.Task[None] | None = None
        self._ingress_retry_deadline: datetime | None = None
        self._ingress_retry_lock = asyncio.Lock()
        self._lease_owner_token: str | None = None
        self._sender_registered = False

    async def start(self) -> None:
        """Connect Telethon, register intake handlers, and start background worker tasks."""

        await _start_runtime_impl(
            self,
            validate_profile_policy=validate_telethon_profile_policy,
            resolve_credentials=resolve_telethon_credentials,
            persist_identity_state=persist_telethon_identity_state,
            import_telethon_module=import_telethon,
        )

    async def stop(self) -> None:
        """Stop intake worker, unregister sender, and disconnect Telethon."""

        await _stop_runtime_impl(self)

    async def probe_identity(self) -> TelethonUserIdentity:
        """Return the currently connected identity or connect for a one-off live probe."""

        return await _probe_identity_impl(
            self,
            resolve_credentials=resolve_telethon_credentials,
        )

    async def reset_state(self) -> bool:
        """Delete persisted Telethon runtime state file when present."""

        return await _reset_state_impl(self)

    async def _run_until_disconnected(self, client: TelethonClientLike) -> None:
        """Translate Telethon disconnect failures into structured service errors."""

        await _run_until_disconnected_impl(
            self,
            client=client,
            persist_identity_state=persist_telethon_identity_state,
        )

    async def _on_new_message(self, event: object) -> None:
        """Normalize one Telethon event and enqueue it for watcher and ingress handling."""

        await _on_new_message_impl(self, event=event)

    def _needs_live_sender_registration(self) -> bool:
        """Return whether the current runtime mode needs a live sender registration."""

        return _needs_live_sender_registration_impl(self)

    async def _watcher_flush_loop(self) -> None:
        """Flush watcher buffers periodically until the runtime stops."""

        await _watcher_flush_loop_impl(self)

    async def _watcher_refresh_loop(self) -> None:
        """Refresh watched-dialog snapshots periodically until the runtime stops."""

        await _watcher_refresh_loop_impl(self)

    async def _worker_loop(self) -> None:
        """Drain the inbound queue through journal claiming and ingress batching."""

        await _worker_loop_impl(self)

    async def _handle_inbound_event(self, item: _QueuedInboundEvent) -> None:
        """Flush one queued inbound event as a single-event batch."""

        await _handle_inbound_event_impl(self, item)

    async def _flush_inbound_batch(self, batch: ChannelIngressBatch) -> None:
        """Route one ingress batch through AgentLoop and optional same-chat replies."""

        await _flush_inbound_batch_impl(
            self,
            batch=batch,
            resolve_runtime_target_fn=resolve_runtime_target,
        )

    async def _handle_ingress_batch_error(
        self,
        batch: ChannelIngressBatch,
        exc: Exception,
    ) -> None:
        """Handle one failed ingress batch with deferred retry when possible."""

        await _handle_ingress_batch_error_impl(self, batch=batch, exc=exc)

    async def _persist_pending_ingress_event(self, event: ChannelIngressEvent) -> bool:
        """Persist one ingress event for deferred replay."""

        return await _persist_pending_ingress_event_impl(self, event)

    async def _release_pending_ingress_batch(self, batch: ChannelIngressBatch) -> None:
        """Release one persisted ingress batch after it has been processed."""

        await _release_pending_ingress_batch_impl(self, batch)

    async def _restore_pending_ingress_events(self) -> None:
        """Restore persisted ingress events once for the current runtime session."""

        await _restore_pending_ingress_events_impl(self)

    async def _flush_persisted_pending_ingress(self) -> None:
        """Replay persisted pending ingress events through the coalescer."""

        await _flush_persisted_pending_ingress_impl(self)

    async def _spill_overflow_event(self, item: _QueuedInboundEvent) -> None:
        """Persist one overflowed inbound event instead of dropping it."""

        await _spill_overflow_event_impl(self, item)

    async def _schedule_pending_ingress_retry(self, *, retry_after_sec: int) -> None:
        """Schedule a deferred retry for pending ingress events."""

        await _schedule_pending_ingress_retry_impl(self, retry_after_sec=retry_after_sec)

    async def _retry_pending_ingress_after_deadline(self, *, deadline: datetime) -> None:
        """Flush pending ingress events after the computed retry deadline."""

        await _retry_pending_ingress_after_deadline_impl(self, deadline=deadline)

    def _to_ingress_event(self, item: _QueuedInboundEvent) -> ChannelIngressEvent:
        """Convert one queued inbound event into an ingress event payload."""

        return _to_ingress_event_impl(self, item)

    @staticmethod
    def _build_batch_client_msg_id(batch: ChannelIngressBatch) -> str:
        """Build a deterministic idempotency key for one ingress batch."""

        return _build_batch_client_msg_id(batch)

    async def _refresh_watched_dialogs(self, *, client: TelethonClientLike | None = None) -> None:
        """Refresh watched-dialog state from the live client."""

        await _refresh_watched_dialogs_impl(self, client=client)

    async def _normalize_watched_event(self, event: object) -> TelethonWatchedEvent | None:
        """Normalize one incoming event into watcher-buffer payload."""

        return await _normalize_watched_event_impl(self, event=event)

    async def _buffer_watched_event(self, event: TelethonWatchedEvent) -> None:
        """Append one watcher event to the in-memory buffer if it is not already tracked."""

        await _buffer_watched_event_impl(self, event=event)

    async def _flush_watcher_batch(self) -> None:
        """Flush one watcher digest batch through AgentLoop and optional delivery."""

        await _flush_watcher_batch_impl(
            self,
            resolve_runtime_target_fn=resolve_runtime_target,
        )

    async def _resolve_watcher_runtime_target(
        self,
        *,
        resolve_runtime_target_fn: Any = resolve_runtime_target,
    ) -> RuntimeTarget:
        """Resolve the runtime target for watcher digests with endpoint fallback."""

        return await _resolve_watcher_runtime_target_impl(
            self,
            resolve_runtime_target_fn=resolve_runtime_target_fn,
        )

    async def _pop_watcher_batch(self) -> tuple[TelethonWatchedEvent, ...]:
        """Pop the next watcher batch from the in-memory buffer."""

        return await _pop_watcher_batch_impl(self)

    async def _restore_watcher_batch(self, batch: tuple[TelethonWatchedEvent, ...]) -> None:
        """Restore a failed watcher batch to the in-memory buffer."""

        await _restore_watcher_batch_impl(self, batch=batch)

    def _trim_watcher_buffer_locked(self) -> None:
        """Trim the watcher buffer down to the configured max size."""

        _trim_watcher_buffer_locked_impl(self)

    def _build_watcher_client_msg_id(self, batch: tuple[TelethonWatchedEvent, ...]) -> str:
        """Build a deterministic idempotency key for one watcher digest batch."""

        return _build_watcher_client_msg_id(self, batch=batch)

    async def _normalize_event(self, event: object) -> TelethonInboundMessage | None:
        """Normalize one reactive Telethon event into ingress payload."""

        return await _normalize_event_impl(self, event=event)

    async def _resolve_reactive_chat_match_text(self, event: object) -> str:
        """Resolve best-effort chat match text for reactive inbound filtering."""

        return await _resolve_reactive_chat_match_text_impl(event=event)

    async def _send_text_via_live_client(
        self,
        target: Any,
        text: str,
    ) -> dict[str, object]:
        """Send one outbound message through the live Telethon client."""

        return await _send_text_via_live_client_impl(self, target=target, text=text)

    @staticmethod
    def _extract_flood_wait_retry_after_sec(exc: Exception) -> int | None:
        """Extract retry_after_sec from one raw Telethon flood-wait exception."""

        return _extract_flood_wait_retry_after_sec(exc)

    @staticmethod
    def _extract_delivery_retry_after_sec(exc: Exception) -> int | None:
        """Extract retry_after_sec from one structured delivery exception."""

        return _extract_delivery_retry_after_sec(exc)

    @staticmethod
    def _resolve_outbound_entity(peer_id: str | None) -> object | None:
        """Normalize one outbound peer id into Telethon entity input."""

        return _resolve_outbound_entity(peer_id)

    @staticmethod
    def _parse_last_batch_message_id(batch: ChannelIngressBatch) -> int | None:
        """Return the last message id in one ingress batch when present."""

        return _parse_last_batch_message_id(batch)
