"""Tests for shared delayed ingress batching and cooldown."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from afkbot.services.channels.endpoint_contracts import ChannelIngressBatchConfig
from afkbot.services.channels.ingress_coalescer import ChannelIngressCoalescer, ChannelIngressEvent


def _event(*, message_id: str, text: str) -> ChannelIngressEvent:
    return ChannelIngressEvent(
        endpoint_id="endpoint-1",
        transport="telegram_user",
        account_id="tg-user",
        peer_id="42",
        thread_id=None,
        user_id="777",
        event_key=f"event:{message_id}",
        message_id=message_id,
        text=text,
        observed_at=datetime.now(UTC).isoformat(),
    )


@pytest.mark.asyncio
async def test_ingress_coalescer_merges_messages_arriving_during_cooldown() -> None:
    """Cooldown should defer the next flush and let more messages join the delayed batch."""

    flushed: list[list[str]] = []

    async def _on_flush(batch) -> None:  # type: ignore[no-untyped-def]
        flushed.append([item.message_id for item in batch.events])

    coalescer = ChannelIngressCoalescer(
        config=ChannelIngressBatchConfig(
            enabled=True,
            debounce_ms=100,
            cooldown_sec=1,
            max_batch_size=20,
            max_buffer_chars=4000,
        ),
        on_flush=_on_flush,
    )

    await coalescer.enqueue(_event(message_id="1", text="hello"))
    await asyncio.sleep(0.18)
    assert flushed == [["1"]]

    await coalescer.enqueue(_event(message_id="2", text="second"))
    await asyncio.sleep(0.1)
    await coalescer.enqueue(_event(message_id="3", text="third"))
    await asyncio.sleep(0.2)
    assert flushed == [["1"]]

    await asyncio.sleep(0.9)
    assert flushed == [["1"], ["2", "3"]]


@pytest.mark.asyncio
async def test_ingress_coalescer_persists_events_even_when_batching_disabled() -> None:
    """Disabled batching should still persist and release ingress events for recovery semantics."""

    persisted: list[str] = []
    released: list[list[str]] = []
    flushed: list[list[str]] = []

    async def _persist_event(event: ChannelIngressEvent) -> bool:
        persisted.append(event.message_id)
        return True

    async def _release_batch(batch) -> None:  # type: ignore[no-untyped-def]
        released.append([item.message_id for item in batch.events])

    async def _on_flush(batch) -> None:  # type: ignore[no-untyped-def]
        flushed.append([item.message_id for item in batch.events])

    coalescer = ChannelIngressCoalescer(
        config=ChannelIngressBatchConfig(
            enabled=False,
            debounce_ms=100,
            cooldown_sec=0,
            max_batch_size=20,
            max_buffer_chars=4000,
        ),
        on_flush=_on_flush,
        persist_event=_persist_event,
        release_batch=_release_batch,
    )

    await coalescer.enqueue(_event(message_id="1", text="hello"))

    assert persisted == ["1"]
    assert flushed == [["1"]]
    assert released == [["1"]]
