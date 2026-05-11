"""Outbound WebSocket client for AFKBOT Cloud managed containers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
import json
import logging
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from afkbot.services.error_logging import redact_log_text
from afkbot.settings import Settings
from afkbot.version import load_cli_version_info

logger = logging.getLogger(__name__)

CommandHandler = Callable[["CloudRuntimeCommand"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class CloudRuntimeCommand:
    """One command delivered by the AFKBOT Cloud control plane.

    :param command: Command name.
    :param command_id: Optional control-plane command id for correlation.
    :param payload: Command payload.
    :return: None.
    """

    command: str
    command_id: str | None
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CloudRuntimeGatewayConfig:
    """Validated cloud runtime gateway connection settings."""

    url: str
    token: str
    heartbeat_interval_sec: float
    reconnect_initial_sec: float
    reconnect_max_sec: float
    message_max_bytes: int
    fail_closed: bool = True


class CloudRuntimeGatewayClient:
    """Maintain one outbound WebSocket link to the AFKBOT Cloud control plane."""

    def __init__(
        self,
        *,
        config: CloudRuntimeGatewayConfig,
        command_handler: CommandHandler | None = None,
    ) -> None:
        """Create a runtime gateway client.

        :param config: Validated gateway config.
        :param command_handler: Optional callback for cloud-originated commands.
        :return: None.
        """

        self._config = config
        self._command_handler = command_handler
        self._stop_event = asyncio.Event()
        self._ready_event = asyncio.Event()
        self._send_lock = asyncio.Lock()
        self._ws: Any | None = None

    @classmethod
    def from_settings(
        cls,
        *,
        settings: Settings,
        command_handler: CommandHandler | None = None,
    ) -> CloudRuntimeGatewayClient | None:
        """Build a gateway client only when cloud managed mode is enabled.

        :param settings: Runtime settings.
        :param command_handler: Optional callback for cloud-originated commands.
        :return: Client instance or None for local/self-hosted mode.
        """

        if not settings.cloud_gateway_enabled:
            return None
        if not settings.control_ws_url or not settings.runtime_ws_token:
            raise ValueError("cloud runtime gateway requires control_ws_url and runtime_ws_token")
        return cls(
            config=CloudRuntimeGatewayConfig(
                url=settings.control_ws_url,
                token=settings.runtime_ws_token,
                heartbeat_interval_sec=settings.cloud_gateway_heartbeat_interval_sec,
                reconnect_initial_sec=settings.cloud_gateway_reconnect_initial_sec,
                reconnect_max_sec=settings.cloud_gateway_reconnect_max_sec,
                message_max_bytes=settings.cloud_gateway_message_max_bytes,
                fail_closed=True,
            ),
            command_handler=command_handler,
        )

    def request_stop(self) -> None:
        """Ask the reconnect loop to stop.

        :param: None.
        :return: None.
        """

        self._stop_event.set()

    def set_command_handler(self, command_handler: CommandHandler | None) -> None:
        """Install or replace the command handler.

        :param command_handler: Command handler callback or None.
        :return: None.
        """

        self._command_handler = command_handler

    async def run(self) -> None:
        """Run the reconnect loop until shutdown is requested.

        :param: None.
        :return: None.
        """

        delay = self._config.reconnect_initial_sec
        while not self._stop_event.is_set():
            try:
                await self._run_once()
                delay = self._config.reconnect_initial_sec
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._config.fail_closed:
                    raise RuntimeError("cloud runtime gateway connection failed") from exc
                logger.warning(
                    "Cloud runtime gateway connection failed: %s",
                    redact_log_text(str(exc)),
                )
            else:
                if self._config.fail_closed and not self._stop_event.is_set():
                    raise RuntimeError("cloud runtime gateway disconnected")
            if self._stop_event.is_set():
                break
            await self._sleep_reconnect(delay)
            delay = min(delay * 2, self._config.reconnect_max_sec)

    async def wait_until_ready(self, *, timeout_sec: float) -> None:
        """Wait until the first gateway handshake and heartbeat are sent.

        :param timeout_sec: Maximum wait time in seconds.
        :return: None.
        """

        await asyncio.wait_for(self._ready_event.wait(), timeout=timeout_sec)

    async def send_event(
        self,
        *,
        event_type: str,
        message: str = "",
        severity: str = "info",
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Send one runtime event when the gateway is currently connected.

        :param event_type: Stable event type.
        :param message: Human-readable event message.
        :param severity: Event severity.
        :param payload: JSON-safe event payload.
        :return: None.
        """

        await self._send(
            {
                "type": "event",
                "event_type": event_type,
                "severity": severity,
                "message": redact_log_text(message),
                "payload": _redact_payload(payload or {}),
            }
        )

    async def send_log(self, *, line: str, level: str = "info", stream: str = "runtime") -> None:
        """Send one runtime log line when connected.

        :param line: Log line to redact and forward.
        :param level: Log level.
        :param stream: Logical stream name.
        :return: None.
        """

        await self._send(
            {
                "type": "log",
                "stream": stream[:24],
                "level": level[:24],
                "line": redact_log_text(line),
            }
        )

    async def send_chat_result(
        self,
        *,
        command_id: str | None,
        message_id: str | None,
        content: str,
        role: str = "assistant",
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Send one chat result back to the control plane.

        :param command_id: Control-plane command id.
        :param message_id: Source message id.
        :param content: Assistant response content.
        :param role: Message role.
        :param payload: Additional metadata.
        :return: None.
        """

        safe_payload = _redact_payload(payload or {})
        if message_id:
            safe_payload = {**safe_payload, "message_id": message_id}
        await self._send(
            {
                "type": "chat.result",
                "command_id": command_id or "",
                "role": role[:24],
                "content": redact_log_text(content),
                "payload": safe_payload,
            }
        )

    async def send_task_update(
        self,
        *,
        command_id: str | None,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        """Send one non-terminal task state update.

        :param command_id: Control-plane command id.
        :param message: Human-readable state message.
        :param payload: Task state payload.
        :return: None.
        """

        await self._send_task_message(
            message_type="task.update",
            command_id=command_id,
            message=message,
            payload=payload,
        )

    async def send_task_result(
        self,
        *,
        command_id: str | None,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        """Send one terminal task result.

        :param command_id: Control-plane command id.
        :param message: Human-readable result message.
        :param payload: Task result payload.
        :return: None.
        """

        await self._send_task_message(
            message_type="task.result",
            command_id=command_id,
            message=message,
            payload=payload,
        )

    async def _run_once(self) -> None:
        headers = {"Authorization": f"Bearer {self._config.token}"}
        async with websockets.connect(
            self._config.url,
            additional_headers=headers,
            max_size=self._config.message_max_bytes,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            try:
                await self._send_hello()
                await self._send_heartbeat()
                self._ready_event.set()
                await self.send_event(
                    event_type="runtime.gateway.ready",
                    message="Runtime connected to cloud control plane.",
                )
                await self.send_log(line="Cloud runtime gateway connected.", level="info")
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_loop(),
                    name="afk-cloud-gateway-heartbeat",
                )
                try:
                    await self._receive_loop()
                finally:
                    heartbeat_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat_task
            finally:
                self._ws = None

    async def _receive_loop(self) -> None:
        while not self._stop_event.is_set():
            if self._ws is None:
                return
            try:
                raw_message = await self._ws.recv()
            except ConnectionClosed:
                return
            message = _parse_message(raw_message)
            if message.get("type") != "command":
                continue
            command = str(message.get("command", ""))
            command_id = message.get("command_id")
            payload = message.get("payload", {})
            if not isinstance(payload, dict):
                payload = {"value": payload}
            if self._command_handler is not None and command:
                await self._command_handler(
                    CloudRuntimeCommand(
                        command=command,
                        command_id=str(command_id) if command_id else None,
                        payload=payload,
                    )
                )

    async def _send_hello(self) -> None:
        version = load_cli_version_info().version
        await self._send(
            {
                "type": "hello",
                "runtime_version": version,
                "protocol_version": "2026-05-08",
                "capabilities": [
                    "heartbeat",
                    "log",
                    "event",
                    "task.update",
                    "task.result",
                    "chat.result",
                    "command.chat.message",
                    "command.task.create",
                    "command.task.retry",
                    "command.task.cancel",
                    "command.shutdown",
                ],
            }
        )

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._send_heartbeat()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.heartbeat_interval_sec,
                )
            except asyncio.TimeoutError:
                continue

    async def _send_heartbeat(self) -> None:
        version = load_cli_version_info().version
        await self._send(
            {
                "type": "heartbeat",
                "health": {
                    "status": "running",
                    "runtime_version": version,
                },
            }
        )

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._ws is None:
            return
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        if len(encoded.encode("utf-8")) > self._config.message_max_bytes:
            raise ValueError("cloud runtime gateway message exceeds configured size limit")
        async with self._send_lock:
            await self._ws.send(encoded)

    async def _send_task_message(
        self,
        *,
        message_type: str,
        command_id: str | None,
        message: str,
        payload: dict[str, Any],
    ) -> None:
        await self._send(
            {
                "type": message_type,
                "command_id": command_id or "",
                "message": redact_log_text(message),
                "payload": _redact_payload(payload),
            }
        )

    async def _sleep_reconnect(self, delay: float) -> None:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            return


def _parse_message(raw_message: str | bytes) -> dict[str, Any]:
    if isinstance(raw_message, bytes):
        raw_message = raw_message.decode("utf-8", errors="replace")
    try:
        message = json.loads(raw_message)
    except json.JSONDecodeError:
        return {}
    if not isinstance(message, dict):
        return {}
    return message


def _redact_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        return redact_log_text(payload)
    if isinstance(payload, list):
        return [_redact_payload(item) for item in payload]
    if isinstance(payload, dict):
        return {str(key): _redact_payload(value) for key, value in payload.items()}
    return payload
