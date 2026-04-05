"""Tests for Task Flow lease refresh runtime helpers."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from afkbot.services.task_flow.lease_runtime import TaskFlowLeaseError, run_with_lease_refresh


async def test_run_with_lease_refresh_surfaces_refresh_failure_cause() -> None:
    """Lease refresh failures should preserve a short diagnostic cause."""

    async def _run() -> str:
        await asyncio.sleep(10)
        return "done"

    async def _refresh() -> bool:
        raise RuntimeError("sqlite is locked")

    with pytest.raises(TaskFlowLeaseError, match="sqlite is locked"):
        await run_with_lease_refresh(
            run=_run,
            refresh=_refresh,
            ttl=timedelta(seconds=1),
        )
