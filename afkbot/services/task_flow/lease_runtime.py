"""Lease refresh runtime for long-running Task Flow executions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar

TValue = TypeVar("TValue")


@dataclass(slots=True)
class TaskFlowLeaseError(RuntimeError):
    """Structured error raised when a task lease is lost or cannot refresh."""

    error_code: str
    reason: str

    def __post_init__(self) -> None:
        super().__init__(self.reason)


async def run_with_lease_refresh(
    *,
    run: Callable[[], Awaitable[TValue]],
    refresh: Callable[[], Awaitable[bool]],
    ttl: timedelta,
) -> TValue:
    """Run one task while periodically refreshing the current claim lease."""

    stop_signal = asyncio.Event()
    refresh_interval_sec = max(1.0, ttl.total_seconds() / 3.0)
    lease_error: TaskFlowLeaseError | None = None
    run_task: asyncio.Future[TValue] = asyncio.ensure_future(run())

    async def _refresh_loop() -> None:
        nonlocal lease_error
        while not stop_signal.is_set():
            await asyncio.sleep(refresh_interval_sec)
            if stop_signal.is_set():
                return
            try:
                refreshed = await refresh()
            except Exception as exc:
                lease_error = TaskFlowLeaseError(
                    error_code="taskflow_lease_refresh_failed",
                    reason="Failed to refresh task claim lease",
                )
                if not run_task.done():
                    run_task.cancel()
                raise lease_error from exc
            if not refreshed:
                lease_error = TaskFlowLeaseError(
                    error_code="taskflow_lease_lost",
                    reason="Task claim lease lost during execution",
                )
                if not run_task.done():
                    run_task.cancel()
                return

    refresh_task = asyncio.create_task(_refresh_loop())
    try:
        try:
            result = await run_task
        except asyncio.CancelledError as exc:
            if lease_error is not None:
                raise lease_error from exc
            raise
        if lease_error is not None:
            raise lease_error
        return result
    finally:
        stop_signal.set()
        refresh_task.cancel()
        with suppress(asyncio.CancelledError):
            await refresh_task
