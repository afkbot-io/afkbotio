"""Logging-focused tests for runtime daemon failure paths."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from afkbot.services.automations.runtime_daemon import RuntimeDaemon
from tests.services.automations._runtime_harness import (
    FailingCronRuntimeService,
    FailingWebhookRuntimeService,
    build_settings,
    request_json,
    webhook_path,
)


async def test_runtime_daemon_logs_webhook_worker_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Daemon should log webhook processing failures instead of silently dropping them."""

    service = FailingWebhookRuntimeService()
    settings = build_settings(tmp_path)
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _profile_id, _token: asyncio.sleep(0, result=True),
    )
    await daemon.start()
    try:
        with caplog.at_level(logging.ERROR, logger="afkbot.services.automations.runtime_daemon"):
            status, payload = await request_json(
                host=settings.runtime_host,
                port=daemon.bound_port,
                method="POST",
                path=webhook_path(),
                body='{"event_id":"evt-log-webhook"}',
            )
            assert status == 202
            assert payload == {"accepted": True}
            for _ in range(40):
                if any("automation_runtime_webhook_task_failed" in rec.message for rec in caplog.records):
                    break
                await asyncio.sleep(0.01)
        assert any("automation_runtime_webhook_task_failed" in rec.message for rec in caplog.records)
    finally:
        await daemon.stop()


async def test_runtime_daemon_logs_cron_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Daemon should log cron loop failures instead of silently dropping them."""

    service = FailingCronRuntimeService()
    settings = build_settings(tmp_path, runtime_cron_interval_sec=0.05)
    daemon = RuntimeDaemon(
        settings=settings,
        service=service,
        webhook_token_validator=lambda _profile_id, _token: asyncio.sleep(0, result=True),
    )
    await daemon.start()
    try:
        with caplog.at_level(logging.ERROR, logger="afkbot.services.automations.runtime_daemon"):
            for _ in range(80):
                if any("automation_runtime_cron_tick_failed" in rec.message for rec in caplog.records):
                    break
                await asyncio.sleep(0.01)
        assert any("automation_runtime_cron_tick_failed" in rec.message for rec in caplog.records)
    finally:
        await daemon.stop()
