"""Telegram Bot API polling facade over shared routing, batching, and AgentLoop services."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import Any

from afkbot.services.agent_loop.api_runtime import run_chat_turn
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.runtime import AppRuntime
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    RuntimeTarget,
    resolve_runtime_target,
)
from afkbot.services.channels.endpoint_contracts import (
    ChannelEndpointConfig,
    TelegramPollingEndpointConfig,
)
from afkbot.services.channels.endpoint_service import telegram_polling_state_path_for
from afkbot.services.channels.ingress_coalescer import ChannelIngressCoalescer
from afkbot.services.channels.service import ChannelDeliveryService
from afkbot.services.channels.telegram_polling_runtime import TelegramPollingRuntimeMixin
from afkbot.services.channels.telegram_polling_support import (
    TelegramBotIdentity,
    TelegramGroupTriggerMode,
)
from afkbot.services.channels.telegram_timeouts import is_telegram_action_timeout_reason
from afkbot.services.profile_id import validate_profile_id
from afkbot.settings import Settings

_LOGGER = logging.getLogger(__name__)
_TELEGRAM_POLLING_SESSION_ID = "telegram-polling"


class TelegramPollingServiceError(ValueError):
    """Structured Telegram polling adapter failure."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


class TelegramPollingService(TelegramPollingRuntimeMixin):
    """Long-poll Telegram Bot API and forward eligible messages through routing and AgentLoop."""

    def __init__(
        self,
        settings: Settings,
        *,
        endpoint: ChannelEndpointConfig | TelegramPollingEndpointConfig,
        state_path: Path | None = None,
        app_runtime: AppRuntime | None = None,
        channel_delivery_service: ChannelDeliveryService | None = None,
        run_chat_turn_fn: Any = run_chat_turn,
    ) -> None:
        self._settings = settings
        self._endpoint = TelegramPollingEndpointConfig.model_validate(endpoint.model_dump())
        self._app_runtime = app_runtime or AppRuntime(settings)
        self._channel_delivery_service = channel_delivery_service or ChannelDeliveryService(settings)
        self._run_chat_turn = run_chat_turn_fn
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._bot_identity: TelegramBotIdentity | None = None
        self._next_update_offset: int | None = None
        self._persisted_next_update_offset: int | None = None
        self._pending_update_order: deque[int] = deque()
        self._pending_update_status: dict[int, bool] = {}
        self._delivery_retry_tasks: dict[str, asyncio.Task[None]] = {}
        self._delivery_retry_attempts: dict[str, int] = {}
        self._offset_lock = asyncio.Lock()
        self._runtime_profile_id = validate_profile_id(self._endpoint.profile_id)
        self._credential_profile_key = self._endpoint.credential_profile_key.strip()
        self._account_id = self._endpoint.account_id.strip()
        self._state_path = state_path or telegram_polling_state_path_for(
            settings,
            endpoint_id=self._endpoint.endpoint_id,
        )
        self._group_trigger_mode: TelegramGroupTriggerMode = self._endpoint.group_trigger_mode
        self._ingress_coalescer = ChannelIngressCoalescer(
            config=self._endpoint.ingress_batch,
            on_flush=self._flush_inbound_batch,
            on_flush_error=self._handle_ingress_batch_error,
            persist_event=self._persist_pending_ingress_event,
            release_batch=self._release_pending_ingress_batch,
        )
        self._pending_restored = False
        if not self._credential_profile_key:
            raise TelegramPollingServiceError(
                error_code="telegram_polling_invalid_credential_profile",
                reason="telegram_polling_credential_profile_key is required",
            )
        if not self._account_id:
            raise TelegramPollingServiceError(
                error_code="telegram_polling_invalid_account_id",
                reason="telegram_polling_account_id is required",
            )

    async def start(self) -> None:
        """Start the background polling loop after identity and offset bootstrap."""

        if self._task is not None:
            return
        self._bot_identity = await self._resolve_bot_identity()
        await self._reset_offset_tracking(await self._load_next_update_offset())
        await self._restore_pending_ingress_events()
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._poll_loop(),
            name=f"telegram-bot-polling:{self._endpoint.endpoint_id}",
        )

    async def stop(self) -> None:
        """Stop the background polling loop and cancel in-flight retry tasks."""

        task = self._task
        if task is None:
            return
        self._task = None
        self._stop_event.set()
        retry_tasks = list(self._delivery_retry_tasks.values())
        self._delivery_retry_tasks.clear()
        self._delivery_retry_attempts.clear()
        for retry_task in retry_tasks:
            retry_task.cancel()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for retry_task in retry_tasks:
            with suppress(asyncio.CancelledError):
                await retry_task
        await self._ingress_coalescer.flush_all()
        self._pending_restored = False

    async def poll_once(self) -> int:
        """Fetch one update batch and process all supported inbound messages."""

        if self._bot_identity is None:
            self._bot_identity = await self._resolve_bot_identity()
        if self._next_update_offset is None:
            await self._reset_offset_tracking(await self._load_next_update_offset())
        await self._restore_pending_ingress_events()
        updates = await self._fetch_updates()
        await self._process_updates(updates)
        if self._task is None and self._endpoint.ingress_batch.enabled:
            await self._ingress_coalescer.flush_all()
        return len(updates)

    async def probe_identity(self) -> TelegramBotIdentity:
        """Run a live Telegram `getMe` probe and return bot identity."""

        identity = await self._resolve_bot_identity()
        self._bot_identity = identity
        return identity

    async def reset_saved_offset(self) -> bool:
        """Delete any persisted polling offset and reset in-memory tracking state."""

        await self._reset_offset_tracking(None)
        if not self._state_path.exists():
            return False
        self._state_path.unlink()
        return True

    async def _poll_loop(self) -> None:
        """Continuously poll Telegram updates until the service is stopped."""

        try:
            while not self._stop_event.is_set():
                try:
                    processed = await self.poll_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.exception("telegram_polling_iteration_failed")
                    await asyncio.sleep(self._settings.telegram_polling_error_backoff_ms / 1000.0)
                    continue
                if processed == 0:
                    await asyncio.sleep(self._settings.telegram_polling_idle_sleep_ms / 1000.0)
        except asyncio.CancelledError:
            raise

    async def _resolve_bot_identity(self) -> TelegramBotIdentity:
        """Resolve bot identity via Telegram `getMe` and validate the payload."""

        result = await self._app_runtime.run(
            app="telegram",
            action="get_me",
            ctx=self._app_context(timeout_sec=min(10, self._settings.tool_timeout_max_sec)),
            params={},
        )
        if not result.ok:
            raise TelegramPollingServiceError(
                error_code=result.error_code or "telegram_polling_get_me_failed",
                reason=result.reason or "Telegram polling getMe failed",
            )
        payload = result.payload
        bot_id = payload.get("id")
        if not isinstance(bot_id, int):
            raise TelegramPollingServiceError(
                error_code="telegram_polling_invalid_identity",
                reason="Telegram getMe returned invalid bot id",
            )
        username = payload.get("username")
        return TelegramBotIdentity(
            bot_id=bot_id,
            username=str(username).strip() or None if username is not None else None,
        )

    async def _fetch_updates(self) -> list[dict[str, object]]:
        """Fetch one Telegram update batch and normalize payload shape."""

        async with self._offset_lock:
            next_update_offset = self._next_update_offset
        timeout_sec = min(
            max(
                self._settings.telegram_polling_timeout_sec + 10,
                self._settings.tool_timeout_default_sec,
            ),
            self._settings.tool_timeout_max_sec,
        )
        result = await self._app_runtime.run(
            app="telegram",
            action="get_updates",
            ctx=self._app_context(timeout_sec=timeout_sec),
            params={
                "limit": self._settings.telegram_polling_limit,
                "timeout": self._settings.telegram_polling_timeout_sec,
                "offset": next_update_offset,
            },
        )
        if not result.ok:
            if self._is_soft_get_updates_timeout(result):
                _LOGGER.warning(
                    "telegram_polling_get_updates_timeout endpoint_id=%s timeout_sec=%s",
                    self._endpoint.endpoint_id,
                    timeout_sec,
                )
                return []
            raise TelegramPollingServiceError(
                error_code=result.error_code or "telegram_polling_get_updates_failed",
                reason=result.reason or "Telegram polling getUpdates failed",
            )
        raw_updates = result.payload.get("updates")
        if not isinstance(raw_updates, list):
            return []
        normalized: list[dict[str, object]] = []
        for item in raw_updates:
            if isinstance(item, dict):
                normalized.append({str(key): value for key, value in item.items()})
        return normalized

    def _is_soft_get_updates_timeout(self, result: Any) -> bool:
        """Return whether one `getUpdates` failure should be treated as idle polling."""

        error_code = getattr(result, "error_code", None)
        reason = getattr(result, "reason", None)
        if error_code != "app_run_failed":
            return False
        if not isinstance(reason, str):
            return False
        return is_telegram_action_timeout_reason(reason)

    async def _resolve_runtime_target(
        self,
        *,
        selectors: RoutingSelectors,
        default_session_id: str,
    ) -> RuntimeTarget:
        """Resolve channel-routing target while keeping monkeypatch compatibility in this module."""

        return await resolve_runtime_target(
            settings=self._settings,
            explicit_profile_id=None,
            explicit_session_id=None,
            resolve_binding=True,
            selectors=selectors,
            default_profile_id=self._runtime_profile_id,
            default_session_id=default_session_id,
        )

    def _app_context(self, *, timeout_sec: int) -> AppRuntimeContext:
        """Build the stable app-runtime context used for Telegram Bot API calls."""

        return AppRuntimeContext(
            profile_id=self._runtime_profile_id,
            session_id=_TELEGRAM_POLLING_SESSION_ID,
            run_id=0,
            credential_profile_key=self._credential_profile_key,
            timeout_sec=timeout_sec,
        )
