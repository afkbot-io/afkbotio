"""Lifecycle and queue orchestration tests for the runtime daemon."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from afkbot.services.automations.runtime_daemon import RuntimeDaemon
from tests.services.automations._runtime_harness import (
    FakeRuntimeService,
    build_settings,
    request_json,
    webhook_path,
)


async def test_runtime_daemon_readyz_fails_when_cron_task_stops(tmp_path: Path) -> None:
    """Readiness should fail when required background cron task is not alive."""

    service = FakeRuntimeService()
    settings = build_settings(tmp_path)
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _token: asyncio.sleep(0, result=True),
    )
    await daemon.start()
    try:
        assert daemon._cron_task is not None  # noqa: SLF001
        daemon._cron_task.cancel()  # noqa: SLF001
        with pytest.raises(asyncio.CancelledError):
            await daemon._cron_task  # noqa: SLF001

        status, payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="GET",
            path="/readyz",
        )
        assert status == 503
        assert payload["error_code"] == "not_ready"
    finally:
        await daemon.stop()


async def test_runtime_daemon_webhook_queue_full(tmp_path: Path) -> None:
    """Webhook endpoint should return 429 when queue capacity is exhausted."""

    service = FakeRuntimeService()
    service.block_webhook = True

    async def token_validator(token: str) -> bool:
        return token == "token-valid"

    settings = build_settings(
        tmp_path,
        runtime_queue_max_size=1,
        runtime_worker_count=1,
    )
    daemon = RuntimeDaemon(settings=settings, service=service, webhook_token_validator=token_validator)
    await daemon.start()
    try:
        first_status, first_payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(),
            headers={"X-AFK-Webhook-Token": "token-valid"},
            body='{"event_id":"evt-1"}',
        )
        assert first_status == 202
        assert first_payload == {"accepted": True}
        await asyncio.wait_for(service.webhook_started.wait(), timeout=1.0)

        second_status, second_payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(),
            headers={"X-AFK-Webhook-Token": "token-valid"},
            body='{"event_id":"evt-2"}',
        )
        assert second_status == 202
        assert second_payload == {"accepted": True}

        third_status, third_payload = await request_json(
            host=settings.runtime_host,
            port=daemon.bound_port,
            method="POST",
            path=webhook_path(),
            headers={"X-AFK-Webhook-Token": "token-valid"},
            body='{"event_id":"evt-3"}',
        )
        assert third_status == 429
        assert third_payload["error_code"] == "queue_full"
    finally:
        service.webhook_blocker.set()
        await daemon.stop()


async def test_runtime_daemon_cron_loop_ticks(tmp_path: Path) -> None:
    """Cron loop should call automation service periodically."""

    service = FakeRuntimeService()
    settings = build_settings(
        tmp_path,
        runtime_cron_interval_sec=0.05,
        runtime_cron_max_due_per_tick=7,
    )
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _: asyncio.sleep(0, result=True),
    )
    await daemon.start()
    try:
        for _ in range(40):
            if service.tick_calls:
                break
            await asyncio.sleep(0.01)
        assert len(service.tick_calls) >= 1
        assert service.tick_limits
        assert service.tick_limits[0] == 7
    finally:
        await daemon.stop()


async def test_runtime_daemon_cron_tick_runs_immediately_on_start(tmp_path: Path) -> None:
    """Cron loop should execute first tick immediately instead of waiting full interval."""

    service = FakeRuntimeService()
    settings = build_settings(
        tmp_path,
        runtime_cron_interval_sec=3600.0,
    )
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _token: asyncio.sleep(0, result=True),
    )
    await daemon.start()
    try:
        for _ in range(40):
            if service.tick_calls:
                break
            await asyncio.sleep(0.01)
        assert len(service.tick_calls) >= 1
    finally:
        await daemon.stop()


async def test_runtime_daemon_start_failure_rolls_back_resources(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Failed start should not leave server/tasks/engines partially running."""

    service = FakeRuntimeService()
    settings = build_settings(tmp_path, runtime_worker_count=1)
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _token: asyncio.sleep(0, result=True),
    )
    created_tasks: list[asyncio.Task[object]] = []
    original_create_task = asyncio.create_task

    def failing_create_task(  # type: ignore[no-untyped-def]
        coro: object,
        *,
        name: str | None = None,
        context: object | None = None,
    ) -> asyncio.Task[object]:
        if name == "automation-runtime-cron":
            if hasattr(coro, "close"):
                coro.close()  # type: ignore[attr-defined]
            raise RuntimeError("cron task create failure")
        task = original_create_task(coro, name=name, context=context)  # type: ignore[arg-type]
        created_tasks.append(task)
        return task

    monkeypatch.setattr(
        "afkbot.services.automations.runtime_daemon.asyncio.create_task",
        failing_create_task,
    )

    with pytest.raises(RuntimeError, match="cron task create failure"):
        await daemon.start()

    assert daemon._server is None  # noqa: SLF001
    assert daemon._cron_task is None  # noqa: SLF001
    assert daemon._worker_tasks == []  # noqa: SLF001
    assert daemon._validation_engine is None  # noqa: SLF001
    assert created_tasks
    assert all(task.done() for task in created_tasks)


async def test_runtime_daemon_shutdown_timeout_cancels_blocked_worker(tmp_path: Path) -> None:
    """Stop should respect configured timeout when workers are blocked."""

    service = FakeRuntimeService()
    service.block_webhook = True

    async def token_validator(token: str) -> bool:
        return token == "token-valid"

    settings = build_settings(
        tmp_path,
        runtime_shutdown_timeout_sec=0.05,
        runtime_cron_interval_sec=3600.0,
    )
    daemon = RuntimeDaemon(settings=settings, service=service, webhook_token_validator=token_validator)
    await daemon.start()
    status, payload = await request_json(
        host=settings.runtime_host,
        port=daemon.bound_port,
        method="POST",
        path=webhook_path(),
        headers={"X-AFK-Webhook-Token": "token-valid"},
        body='{"event_id":"evt-blocked"}',
    )
    assert status == 202
    assert payload == {"accepted": True}

    await asyncio.wait_for(service.webhook_started.wait(), timeout=1.0)
    loop = asyncio.get_running_loop()
    started_at = loop.time()
    await daemon.stop()
    elapsed = loop.time() - started_at
    assert elapsed < 0.5
