"""Lease refresh runtime for long-running Task Flow executions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import timedelta
from typing import TypeVar

TValue = TypeVar("TValue")


class TaskFlowLeaseError(RuntimeError):
    """Structured error raised when a task lease is lost or cannot refresh."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        self.error_code = error_code
        self.reason = reason
        super().__init__(reason)


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
                cause = _lease_refresh_cause(exc)
                lease_error = TaskFlowLeaseError(
                    error_code="taskflow_lease_refresh_failed",
                    reason=(
                        f"Failed to refresh task claim lease: {cause}"
                        if cause
                        else "Failed to refresh task claim lease"
                    ),
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


def _lease_refresh_cause(exc: BaseException) -> str:
    """Return one short diagnostic cause for lease refresh failures."""

    reason = str(getattr(exc, "reason", "") or "").strip()
    if not reason:
        reason = str(exc).strip()
    if not reason:
        reason = type(exc).__name__
    return reason[:200]
