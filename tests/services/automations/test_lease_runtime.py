"""Direct tests for automation lease refresh runtime."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from afkbot.services.automations import AutomationsServiceError
from afkbot.services.automations.lease_runtime import run_with_lease_refresh


async def test_run_with_lease_refresh_fails_fast_on_lease_loss() -> None:
    """Lease-loss during execution should cancel run and raise deterministic error."""

    run_cancelled = asyncio.Event()
    refresh_calls = 0

    async def _run() -> object:
        try:
            await asyncio.sleep(5)
            return {"ok": True}
        finally:
            run_cancelled.set()

    async def _refresh() -> bool:
        nonlocal refresh_calls
        refresh_calls += 1
        return False

    with pytest.raises(AutomationsServiceError) as exc_info:
        await run_with_lease_refresh(
            run=_run,
            refresh=_refresh,
            ttl=timedelta(seconds=1),
        )
    assert exc_info.value.error_code == "automation_lease_lost"
    assert run_cancelled.is_set()
    assert refresh_calls >= 1


async def test_run_with_lease_refresh_times_out_running_task() -> None:
    """A hard run timeout should cancel the task even while its lease is healthy."""

    run_cancelled = asyncio.Event()

    async def _run() -> object:
        try:
            await asyncio.sleep(5)
            return {"ok": True}
        finally:
            run_cancelled.set()

    async def _refresh() -> bool:
        return True

    with pytest.raises(AutomationsServiceError) as exc_info:
        await run_with_lease_refresh(
            run=_run,
            refresh=_refresh,
            ttl=timedelta(seconds=30),
            timeout_sec=0.01,
        )

    assert exc_info.value.error_code == "automation_run_timeout"
    assert run_cancelled.is_set()
