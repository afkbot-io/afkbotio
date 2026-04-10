"""Shared harness for runtime daemon tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from afkbot.settings import Settings


class FakeRuntimeService:
    """Fake automation service collecting webhook/cron calls."""

    def __init__(self) -> None:
        self.webhook_calls: list[tuple[str, str, dict[str, object]]] = []
        self.tick_calls: list[datetime] = []
        self.tick_limits: list[int | None] = []
        self.webhook_started = asyncio.Event()
        self.webhook_blocker = asyncio.Event()
        self.block_webhook = False

    async def trigger_webhook(
        self,
        *,
        profile_id: str,
        token: str,
        payload: Mapping[str, object],
    ) -> object:
        self.webhook_calls.append(
            (
                profile_id,
                token,
                dict(payload),
            )
        )
        self.webhook_started.set()
        if self.block_webhook:
            await self.webhook_blocker.wait()
        return {"ok": True}

    async def tick_cron(
        self,
        *,
        now_utc: datetime,
        max_due_per_tick: int | None = None,
    ) -> object:
        self.tick_calls.append(now_utc)
        self.tick_limits.append(max_due_per_tick)
        return {"ok": True}


class FailingWebhookRuntimeService(FakeRuntimeService):
    """Runtime service test double that raises on webhook execution."""

    async def trigger_webhook(
        self,
        *,
        profile_id: str,
        token: str,
        payload: Mapping[str, object],
    ) -> object:
        _ = profile_id, token, payload
        raise RuntimeError("webhook failure")


class FailingCronRuntimeService(FakeRuntimeService):
    """Runtime service test double that raises on cron execution."""

    async def tick_cron(
        self,
        *,
        now_utc: datetime,
        max_due_per_tick: int | None = None,
    ) -> object:
        _ = now_utc, max_due_per_tick
        raise RuntimeError("cron failure")


def build_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Create one disposable runtime-daemon settings object for tests."""

    base = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime_daemon.db'}",
        root_dir=tmp_path,
        runtime_host="127.0.0.1",
        runtime_port=0,
        runtime_queue_max_size=16,
        runtime_worker_count=1,
        runtime_cron_interval_sec=3600.0,
        runtime_shutdown_timeout_sec=1.0,
        runtime_read_timeout_sec=1.0,
        runtime_max_header_bytes=4096,
        runtime_max_body_bytes=8192,
    )
    if not overrides:
        return base
    return base.model_copy(update=overrides)


def webhook_path(*, profile_id: str = "default", token: str | None = "token-valid") -> str:
    """Build one runtime webhook path for socket-level tests."""

    base = f"/v1/automations/{profile_id}/webhook"
    if token is None:
        return base
    return f"{base}/{token}"


async def request_json(
    *,
    host: str,
    port: int,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: str = "",
) -> tuple[int, dict[str, object]]:
    """Send one raw request and decode its JSON body."""

    status, payload = await request_raw(
        host=host,
        port=port,
        method=method,
        path=path,
        headers=headers,
        body=body,
    )
    assert payload is not None
    return status, payload


async def request_raw(
    *,
    host: str,
    port: int,
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: str = "",
) -> tuple[int, dict[str, object] | None]:
    """Send one raw HTTP request to the daemon and return status + JSON payload."""

    reader, writer = await asyncio.open_connection(host, port)
    encoded_body = body.encode("utf-8")
    merged_headers = {"Host": host, "Connection": "close"}
    if headers is not None:
        merged_headers.update(headers)
    if encoded_body:
        merged_headers["Content-Length"] = str(len(encoded_body))
    header_block = "".join(f"{name}: {value}\r\n" for name, value in merged_headers.items())
    request = f"{method} {path} HTTP/1.1\r\n{header_block}\r\n".encode("ascii") + encoded_body
    writer.write(request)
    await writer.drain()
    raw_response = await reader.read(-1)
    writer.close()
    await writer.wait_closed()

    head, _, raw_body = raw_response.partition(b"\r\n\r\n")
    status_line = head.decode("latin-1").split("\r\n", maxsplit=1)[0]
    status_code = int(status_line.split(" ", maxsplit=2)[1])
    payload = json.loads(raw_body.decode("utf-8")) if raw_body else None
    return status_code, payload
