"""Tests for AFKBOT Cloud runtime gateway client."""

from __future__ import annotations

import json
from typing import Any

import pytest

from afkbot.services.cloud_runtime.gateway import (
    CloudRuntimeCommand,
    CloudRuntimeGatewayClient,
    CloudRuntimeGatewayConfig,
)
from afkbot.settings import Settings


class _FakeWebSocket:
    def __init__(self, *, inbound: list[dict[str, Any]]) -> None:
        self.inbound = inbound
        self.sent: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeWebSocket:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    async def recv(self) -> str:
        if not self.inbound:
            return json.dumps({"type": "noop"})
        return json.dumps(self.inbound.pop(0))


@pytest.mark.asyncio
async def test_gateway_sends_authorized_hello_and_heartbeat(monkeypatch) -> None:
    """Gateway should authenticate with bearer header and emit startup liveness messages."""

    captured: dict[str, object] = {}
    fake_ws = _FakeWebSocket(inbound=[{"type": "command", "command": "shutdown", "payload": {}}])

    def _fake_connect(url: str, **kwargs: object) -> _FakeWebSocket:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return fake_ws

    commands: list[str] = []

    async def _handle_command(command: CloudRuntimeCommand) -> None:
        commands.append(command.command)
        client.request_stop()

    monkeypatch.setattr("afkbot.services.cloud_runtime.gateway.websockets.connect", _fake_connect)
    client = CloudRuntimeGatewayClient(
        config=CloudRuntimeGatewayConfig(
            url="wss://api.example.test/ws/runtime/connect/",
            token="runtime-token",
            heartbeat_interval_sec=60.0,
            reconnect_initial_sec=0.01,
            reconnect_max_sec=0.01,
            message_max_bytes=65536,
        ),
        command_handler=_handle_command,
    )

    await client.run()

    assert captured["url"] == "wss://api.example.test/ws/runtime/connect/"
    assert captured["kwargs"]["additional_headers"] == {"Authorization": "Bearer runtime-token"}
    assert [message["type"] for message in fake_ws.sent][:2] == ["hello", "heartbeat"]
    assert commands == ["shutdown"]


@pytest.mark.asyncio
async def test_gateway_redacts_logs_before_sending() -> None:
    """Gateway log forwarding must redact common secret shapes before network send."""

    fake_ws = _FakeWebSocket(inbound=[])
    client = CloudRuntimeGatewayClient(
        config=CloudRuntimeGatewayConfig(
            url="wss://api.example.test/ws/runtime/connect/",
            token="runtime-token",
            heartbeat_interval_sec=60.0,
            reconnect_initial_sec=0.01,
            reconnect_max_sec=0.01,
            message_max_bytes=65536,
        )
    )
    client._ws = fake_ws  # noqa: SLF001

    await client.send_log(line="token=secret-value Authorization: Bearer abc123")

    assert fake_ws.sent == [
        {
            "type": "log",
            "stream": "runtime",
            "level": "info",
            "line": "token=[REDACTED] Authorization: [REDACTED] [REDACTED]",
        }
    ]


@pytest.mark.asyncio
async def test_gateway_fails_closed_when_connection_fails(monkeypatch) -> None:
    """Managed gateway should not keep a runtime alive without control-plane access."""

    def _fake_connect(url: str, **kwargs: object) -> _FakeWebSocket:
        del url, kwargs
        raise OSError("network down")

    monkeypatch.setattr("afkbot.services.cloud_runtime.gateway.websockets.connect", _fake_connect)
    client = CloudRuntimeGatewayClient(
        config=CloudRuntimeGatewayConfig(
            url="wss://api.example.test/ws/runtime/connect/",
            token="runtime-token",
            heartbeat_interval_sec=60.0,
            reconnect_initial_sec=0.01,
            reconnect_max_sec=0.01,
            message_max_bytes=65536,
        )
    )

    with pytest.raises(RuntimeError, match="cloud runtime gateway connection failed"):
        await client.run()


def test_gateway_is_disabled_for_local_settings(tmp_path) -> None:
    """Local/self-hosted settings should not create a cloud gateway client."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}",
    )

    assert CloudRuntimeGatewayClient.from_settings(settings=settings) is None


def test_gateway_requires_token_in_managed_mode(tmp_path) -> None:
    """Managed mode should fail closed when the runner did not inject a token."""

    with pytest.raises(ValueError, match="runtime_ws_token is required"):
        Settings(
            root_dir=tmp_path,
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}",
            managed_mode=True,
            control_ws_url="wss://api.example.test/ws/runtime/connect/",
        )


def test_gateway_uses_deployment_mode_managed_contract(tmp_path) -> None:
    """Deployment mode managed should enable the cloud gateway without the legacy flag."""

    settings = Settings(
        root_dir=tmp_path,
        deployment_mode="managed",
        db_url="postgresql+asyncpg://bot_role:secret@db.example.com/afkbot_bot_1",
        control_ws_url="wss://api.example.test/ws/runtime/connect/",
        runtime_ws_token="runtime-token",
    )

    assert settings.cloud_gateway_enabled is True
    assert CloudRuntimeGatewayClient.from_settings(settings=settings) is not None


def test_gateway_rejects_insecure_ws_by_default(tmp_path) -> None:
    """Managed gateway tokens must not be sent over cleartext WebSocket by default."""

    with pytest.raises(ValueError, match="wss://"):
        Settings(
            root_dir=tmp_path,
            deployment_mode="managed",
            db_url="postgresql+asyncpg://bot_role:secret@db.example.com/afkbot_bot_1",
            control_ws_url="ws://api.example.test/ws/runtime/connect/",
            runtime_ws_token="runtime-token",
        )


def test_gateway_allows_insecure_ws_only_with_explicit_dev_flag(tmp_path) -> None:
    """Local compose can opt into ws:// explicitly without weakening production defaults."""

    settings = Settings(
        root_dir=tmp_path,
        deployment_mode="managed",
        db_url="postgresql+asyncpg://bot_role:secret@db.example.com/afkbot_bot_1",
        control_ws_url="ws://control-plane:8000/ws/runtime/connect/",
        runtime_ws_token="runtime-token",
        cloud_gateway_allow_insecure_ws=True,
    )

    assert CloudRuntimeGatewayClient.from_settings(settings=settings) is not None
