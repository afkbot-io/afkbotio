"""HTTP ingress helpers for automation runtime daemon."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.services.automations.runtime_http import (
    HttpReadError,
    HttpRequest,
    match_webhook_path,
    parse_webhook_payload,
    read_request,
)
from afkbot.settings import Settings

WebhookTokenValidator = Callable[[str, str], Awaitable[bool]]


class RuntimeDaemonHttpRuntime:
    """HTTP parsing, routing, and webhook validation for runtime daemon."""

    def __init__(
        self,
        *,
        settings: Settings,
        enqueue_task: Callable[[Any], bool],
        is_ready: Callable[[], bool],
        is_shutting_down: Callable[[], bool],
        webhook_token_validator: WebhookTokenValidator | None,
        validation_session_factory_getter: Callable[[], async_sessionmaker[AsyncSession] | None],
        queue_task_factory: Callable[[str, str, Mapping[str, object]], Any],
    ) -> None:
        """Bind routing dependencies used by the runtime daemon HTTP ingress."""

        self._settings = settings
        self._enqueue_task = enqueue_task
        self._is_ready = is_ready
        self._is_shutting_down = is_shutting_down
        self._webhook_token_validator = webhook_token_validator
        self._validation_session_factory_getter = validation_session_factory_getter
        self._queue_task_factory = queue_task_factory

    async def route_request(self, request: HttpRequest) -> tuple[int, Mapping[str, object]]:
        """Route one parsed HTTP request to health/readiness/webhook handlers."""

        if request.method == "GET" and request.path == "/healthz":
            return 200, {"ok": True, "service": "afkbot-runtime"}
        if request.method == "GET" and request.path == "/readyz":
            if self._is_ready():
                return 200, {"ok": True}
            return 503, {"ok": False, "error_code": "not_ready", "reason": "Runtime is not ready"}
        if request.method == "POST":
            target = match_webhook_path(request.path)
            if target is not None:
                return await self._handle_webhook_request(
                    request,
                    profile_id=target.profile_id,
                    token=target.token,
                )
        return 404, {"ok": False, "error_code": "not_found", "reason": "Not found"}

    async def read_request(
        self,
        reader: asyncio.StreamReader,
    ) -> HttpRequest | HttpReadError:
        """Read one HTTP request from stream with daemon runtime limits."""

        return await read_request(
            reader,
            read_timeout_sec=max(self._settings.runtime_read_timeout_sec, 0.1),
            max_header_bytes=max(self._settings.runtime_max_header_bytes, 256),
            max_body_bytes=max(self._settings.runtime_max_body_bytes, 1),
        )

    async def _handle_webhook_request(
        self,
        request: HttpRequest,
        *,
        profile_id: str,
        token: str,
    ) -> tuple[int, Mapping[str, object]]:
        if self._is_shutting_down():
            return 503, {
                "ok": False,
                "error_code": "runtime_shutting_down",
                "reason": "Runtime is shutting down",
            }
        if not await self._is_valid_webhook_token(profile_id=profile_id, token=token):
            return 401, {
                "ok": False,
                "error_code": "invalid_webhook_token",
                "reason": "Missing or invalid webhook token",
            }
        try:
            payload = parse_webhook_payload(request.body)
        except ValueError:
            return 400, {
                "ok": False,
                "error_code": "invalid_payload",
                "reason": "Payload must be a JSON object",
            }
        if not self._enqueue_task(self._queue_task_factory(profile_id, token, payload)):
            return 429, {"ok": False, "error_code": "queue_full", "reason": "Runtime queue is full"}
        return 202, {"accepted": True}

    async def _is_valid_webhook_token(self, *, profile_id: str, token: str) -> bool:
        if self._webhook_token_validator is not None:
            return await self._webhook_token_validator(profile_id, token)
        session_factory = self._validation_session_factory_getter()
        if session_factory is None:
            return False
        async with session_scope(session_factory) as session:
            repo = AutomationRepository(session)
            row = await repo.find_webhook_by_target(profile_id=profile_id, token=token)
            return row is not None
