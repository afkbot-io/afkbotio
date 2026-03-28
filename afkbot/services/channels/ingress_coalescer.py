"""Shared delayed ingress coalescing for channel adapters."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.channels.endpoint_contracts import ChannelIngressBatchConfig


@dataclass(frozen=True, slots=True)
class ChannelIngressEvent:
    """One inbound channel message eligible for delayed batching."""

    endpoint_id: str
    transport: str
    account_id: str
    peer_id: str
    thread_id: str | None
    user_id: str | None
    event_key: str
    message_id: str
    text: str
    observed_at: str
    chat_kind: str | None = None
    source_event_id: str | None = None

    @property
    def conversation_key(self) -> str:
        """Return stable coalescing key preserving current routing selectors."""

        return "|".join(
            (
                self.endpoint_id,
                self.transport,
                self.account_id,
                self.peer_id,
                self.thread_id or "-",
                self.user_id or "-",
            )
        )


@dataclass(frozen=True, slots=True)
class ChannelIngressBatch:
    """One coalesced batch flushed into a single AgentLoop turn."""

    endpoint_id: str
    transport: str
    account_id: str
    peer_id: str
    thread_id: str | None
    user_id: str | None
    chat_kind: str | None
    events: tuple[ChannelIngressEvent, ...]

    @property
    def conversation_key(self) -> str:
        """Return stable coalescing key for one flushed conversation batch."""

        return "|".join(
            (
                self.endpoint_id,
                self.transport,
                self.account_id,
                self.peer_id,
                self.thread_id or "-",
                self.user_id or "-",
            )
        )


AsyncBatchFlush = Any
AsyncBatchError = Any
AsyncPersistEvent = Any
AsyncReleaseBatch = Any


@dataclass(slots=True)
class _PendingBatch:
    endpoint_id: str
    transport: str
    account_id: str
    peer_id: str
    thread_id: str | None
    user_id: str | None
    chat_kind: str | None
    events: list[ChannelIngressEvent] = field(default_factory=list)
    total_chars: int = 0
    task: asyncio.Task[None] | None = None

    def append(self, event: ChannelIngressEvent) -> None:
        self.events.append(event)
        self.total_chars += len(event.text)

    def to_batch(self) -> ChannelIngressBatch:
        return ChannelIngressBatch(
            endpoint_id=self.endpoint_id,
            transport=self.transport,
            account_id=self.account_id,
            peer_id=self.peer_id,
            thread_id=self.thread_id,
            user_id=self.user_id,
            chat_kind=self.chat_kind,
            events=tuple(self.events),
        )


class ChannelIngressCoalescer:
    """Delay and group sequential channel messages before one turn execution."""

    def __init__(
        self,
        *,
        config: ChannelIngressBatchConfig,
        on_flush: AsyncBatchFlush,
        on_flush_error: AsyncBatchError | None = None,
        persist_event: AsyncPersistEvent | None = None,
        release_batch: AsyncReleaseBatch | None = None,
    ) -> None:
        self._config = ChannelIngressBatchConfig.model_validate(config.model_dump())
        self._on_flush = on_flush
        self._on_flush_error = on_flush_error
        self._persist_event = persist_event
        self._release_batch = release_batch
        self._lock = asyncio.Lock()
        self._pending_by_key: dict[str, _PendingBatch] = {}
        self._next_flush_at_by_key: dict[str, float] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def enabled(self) -> bool:
        """Return whether delayed coalescing is enabled."""

        return self._config.enabled

    async def enqueue(self, event: ChannelIngressEvent) -> None:
        """Buffer one event and flush later when debounce window closes."""

        if self._persist_event is not None:
            accepted = await self._persist_event(event)
            if not accepted:
                return
        if not self._config.enabled:
            await self._flush_batch_safe(
                ChannelIngressBatch(
                    endpoint_id=event.endpoint_id,
                    transport=event.transport,
                    account_id=event.account_id,
                    peer_id=event.peer_id,
                    thread_id=event.thread_id,
                    user_id=event.user_id,
                    chat_kind=event.chat_kind,
                    events=(event,),
                ),
                allow_error_handler=False,
            )
            return

        batch_to_flush: ChannelIngressBatch | None = None
        async with self._lock:
            key = event.conversation_key
            pending = self._pending_by_key.get(key)
            if pending is None:
                pending = _PendingBatch(
                    endpoint_id=event.endpoint_id,
                    transport=event.transport,
                    account_id=event.account_id,
                    peer_id=event.peer_id,
                    thread_id=event.thread_id,
                    user_id=event.user_id,
                    chat_kind=event.chat_kind,
                )
                self._pending_by_key[key] = pending
            pending.append(event)
            flush_delay_sec = self._compute_flush_delay_sec_locked(key)
            if pending.task is not None:
                pending.task.cancel()
            if (
                len(pending.events) >= self._config.max_batch_size
                or pending.total_chars >= self._config.max_buffer_chars
            ) and flush_delay_sec <= 0:
                self._pending_by_key.pop(key, None)
                batch_to_flush = pending.to_batch()
            else:
                pending.task = self._spawn_task(self._flush_after_delay(key, flush_delay_sec))
        if batch_to_flush is not None:
            await self._flush_batch_safe(batch_to_flush)

    async def flush_all(self) -> None:
        """Flush all pending batches immediately and wait for active flush tasks."""

        batches: list[ChannelIngressBatch] = []
        async with self._lock:
            for key, pending in list(self._pending_by_key.items()):
                if pending.task is not None:
                    pending.task.cancel()
                batches.append(pending.to_batch())
                self._pending_by_key.pop(key, None)
        for batch in batches:
            await self._flush_batch_safe(batch)
        await self._await_tasks()

    async def restore_pending(self, events: tuple[ChannelIngressEvent, ...]) -> None:
        """Flush previously persisted pending events after runtime restart."""

        if not events:
            return
        for batch in self._split_restore_batches(events):
            await self._flush_batch_safe(batch)

    async def _flush_after_delay(self, key: str, delay_sec: float) -> None:
        try:
            await asyncio.sleep(max(delay_sec, 0.0))
        except asyncio.CancelledError:
            return
        batch: ChannelIngressBatch | None = None
        async with self._lock:
            pending = self._pending_by_key.pop(key, None)
            if pending is not None:
                batch = pending.to_batch()
        if batch is not None:
            await self._flush_batch_safe(batch)

    async def _flush_batch_safe(
        self,
        batch: ChannelIngressBatch,
        *,
        allow_error_handler: bool = True,
    ) -> None:
        try:
            await self._on_flush(batch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if allow_error_handler and self._on_flush_error is not None:
                await self._on_flush_error(batch, exc)
                return
            raise
        if self._config.cooldown_sec > 0:
            async with self._lock:
                self._next_flush_at_by_key[batch.conversation_key] = (
                    time.monotonic() + float(self._config.cooldown_sec)
                )
        if self._release_batch is not None:
            await self._release_batch(batch)

    def _compute_flush_delay_sec_locked(self, key: str) -> float:
        debounce_sec = self._config.debounce_ms / 1000.0
        if self._config.cooldown_sec <= 0:
            return debounce_sec
        cooldown_deadline = self._next_flush_at_by_key.get(key)
        if cooldown_deadline is None:
            return debounce_sec
        cooldown_delay_sec = max(cooldown_deadline - time.monotonic(), 0.0)
        return max(debounce_sec, cooldown_delay_sec)

    def _spawn_task(self, coro: Any) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _await_tasks(self) -> None:
        tasks = tuple(task for task in self._tasks if not task.done())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _split_restore_batches(
        self,
        events: tuple[ChannelIngressEvent, ...],
    ) -> tuple[ChannelIngressBatch, ...]:
        grouped: dict[str, list[ChannelIngressEvent]] = {}
        order: list[str] = []
        for event in events:
            key = event.conversation_key
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(event)
        result: list[ChannelIngressBatch] = []
        for key in order:
            pending_events = grouped[key]
            if not pending_events:
                continue
            if not self._config.enabled:
                for item in pending_events:
                    result.append(
                        ChannelIngressBatch(
                            endpoint_id=item.endpoint_id,
                            transport=item.transport,
                            account_id=item.account_id,
                            peer_id=item.peer_id,
                            thread_id=item.thread_id,
                            user_id=item.user_id,
                            chat_kind=item.chat_kind,
                            events=(item,),
                        )
                    )
                continue
            current: list[ChannelIngressEvent] = []
            current_chars = 0
            for item in pending_events:
                item_chars = len(item.text)
                if current and (
                    len(current) >= self._config.max_batch_size
                    or current_chars + item_chars > self._config.max_buffer_chars
                ):
                    result.append(
                        ChannelIngressBatch(
                            endpoint_id=current[0].endpoint_id,
                            transport=current[0].transport,
                            account_id=current[0].account_id,
                            peer_id=current[0].peer_id,
                            thread_id=current[0].thread_id,
                            user_id=current[0].user_id,
                            chat_kind=current[0].chat_kind,
                            events=tuple(current),
                        )
                    )
                    current = []
                    current_chars = 0
                current.append(item)
                current_chars += item_chars
            if current:
                result.append(
                    ChannelIngressBatch(
                        endpoint_id=current[0].endpoint_id,
                        transport=current[0].transport,
                        account_id=current[0].account_id,
                        peer_id=current[0].peer_id,
                        thread_id=current[0].thread_id,
                        user_id=current[0].user_id,
                        chat_kind=current[0].chat_kind,
                        events=tuple(current),
                    )
                )
        return tuple(result)


def render_channel_ingress_batch_message(batch: ChannelIngressBatch) -> str:
    """Render one coalesced batch into the user message passed to AgentLoop."""

    if len(batch.events) == 1:
        return batch.events[0].text
    parts = [
        "Multiple recent messages arrived from the same conversation.",
        "Consider them together and reply once to the combined context.",
        "",
        "Messages (oldest first):",
    ]
    for index, item in enumerate(batch.events, start=1):
        parts.extend(
            [
                f"[{index}] sender_id: {item.user_id or '-'}",
                f"message_id: {item.message_id}",
                f"observed_at: {item.observed_at}",
                item.text,
                "",
            ]
        )
    return "\n".join(parts).strip()


def build_ingress_batch_context_overrides(batch: ChannelIngressBatch) -> TurnContextOverrides | None:
    """Build runtime metadata/prompt overlay for one coalesced batch."""

    if len(batch.events) <= 1:
        return None
    return TurnContextOverrides(
        runtime_metadata={
            "channel_ingress_batch": {
                "transport": batch.transport,
                "account_id": batch.account_id,
                "peer_id": batch.peer_id,
                "thread_id": batch.thread_id,
                "user_id": batch.user_id,
                "message_count": len(batch.events),
                "message_ids": tuple(item.message_id for item in batch.events),
            }
        },
        prompt_overlay=(
            "The latest inbound turn was coalesced from multiple sequential channel messages "
            "from the same conversation over a short delay. Read them in order and reply once. "
            "Do not mention the batching unless the user explicitly asks."
        ),
    )
