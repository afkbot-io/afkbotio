"""Lease refresh runtime for long-running automation executions."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import timedelta

from afkbot.services.automations.errors import AutomationsServiceError


async def run_with_lease_refresh(
    *,
    run: Callable[[], Awaitable[object]],
    refresh: Callable[[], Awaitable[bool]],
    ttl: timedelta,
) -> object:
    """Run one task while periodically refreshing claim lease."""

    stop_signal = asyncio.Event()
    refresh_interval_sec = max(1.0, ttl.total_seconds() / 3.0)
    lease_error: AutomationsServiceError | None = None
    run_task: asyncio.Task[object] = asyncio.ensure_future(run())

    async def _refresh_loop() -> None:
        nonlocal lease_error
        while not stop_signal.is_set():
            await asyncio.sleep(refresh_interval_sec)
            if stop_signal.is_set():
                return
            try:
                refreshed = await refresh()
            except Exception as exc:
                lease_error = AutomationsServiceError(
                    error_code="automation_lease_refresh_failed",
                    reason="Failed to refresh automation lease",
                )
                if not run_task.done():
                    run_task.cancel()
                raise lease_error from exc
            if not refreshed:
                lease_error = AutomationsServiceError(
                    error_code="automation_lease_lost",
                    reason="Automation lease lost during execution",
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
