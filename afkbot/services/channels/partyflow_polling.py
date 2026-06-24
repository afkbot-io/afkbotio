"""PartyFlow Bot Event Polling channel runtime."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from afkbot.services.agent_loop.api_runtime import run_chat_turn
from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.partyflow.http_api import (
    PARTYFLOW_API_BASE_URL,
    PartyFlowApiError,
    _get_me,
    _poll_events,
)
from afkbot.services.apps.runtime import AppRuntime
from afkbot.services.atomic_writes import atomic_json_write
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    build_channel_default_session_id,
    build_routing_context_overrides,
    resolve_runtime_target,
)
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channels.access_policy import is_channel_message_allowed
from afkbot.services.channels.active_context import build_active_channel_context_overrides
from afkbot.services.channels.context_overrides import build_channel_tool_profile_context_overrides
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.delivery_runtime import (
    CHANNEL_RUNTIME_APP_TOOL_GRANTS,
    PARTYFLOW_CHANNEL_API_HOSTS,
    ChannelDeliveryServiceError,
)
from afkbot.services.channels.endpoint_contracts import (
    ChannelEndpointConfig,
    PartyFlowPollingEndpointConfig,
)
from afkbot.services.channels.endpoint_service import partyflow_polling_state_path_for
from afkbot.services.channels.ingress_coalescer import (
    ChannelIngressBatch,
    ChannelIngressCoalescer,
    ChannelIngressEvent,
    build_ingress_batch_context_overrides,
    render_channel_ingress_batch_message,
)
from afkbot.services.channels.ingress_journal import get_channel_ingress_journal_service
from afkbot.services.channels.ingress_persistence import get_channel_ingress_pending_service
from afkbot.services.channels.reply_policy import should_suppress_channel_reply
from afkbot.services.channels.service import ChannelDeliveryService
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.profile_id import validate_profile_id
from afkbot.settings import Settings

_LOGGER = logging.getLogger(__name__)
_PARTYFLOW_SESSION_ID = "partyflow-polling"


class PartyFlowPollingServiceError(ValueError):
    """Structured PartyFlow polling runtime failure."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PartyFlowBotIdentity:
    """Minimal PartyFlow bot identity used for self-message and mention filtering."""

    bot_id: str
    display_name: str | None = None


class PartyFlowPollingService:
    """Poll PartyFlow Bot Event API and forward eligible messages through AgentLoop."""

    def __init__(
        self,
        settings: Settings,
        *,
        endpoint: ChannelEndpointConfig | PartyFlowPollingEndpointConfig,
        state_path: Path | None = None,
        app_runtime: AppRuntime | None = None,
        channel_delivery_service: ChannelDeliveryService | None = None,
        run_chat_turn_fn: Any = run_chat_turn,
    ) -> None:
        self._settings = settings
        self._endpoint = PartyFlowPollingEndpointConfig.model_validate(endpoint.model_dump())
        self._app_runtime = app_runtime
        self._channel_delivery_service = channel_delivery_service or ChannelDeliveryService(
            settings
        )
        self._run_chat_turn = run_chat_turn_fn
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._bot_identity: PartyFlowBotIdentity | None = None
        self._runtime_profile_id = validate_profile_id(self._endpoint.profile_id)
        self._credential_profile_key = self._endpoint.credential_profile_key.strip()
        self._account_id = self._endpoint.account_id.strip()
        self._cursor = ""
        self._pending_retry_tasks: dict[str, asyncio.Task[None]] = {}
        self._state_path = state_path or partyflow_polling_state_path_for(
            settings,
            endpoint_id=self._endpoint.endpoint_id,
        )
        self._ingress_coalescer = ChannelIngressCoalescer(
            config=self._endpoint.ingress_batch,
            on_flush=self._flush_inbound_batch,
            on_flush_error=self._handle_ingress_batch_error,
            persist_event=self._persist_pending_ingress_event,
            release_batch=self._release_pending_ingress_batch,
        )
        self._pending_restored = False
        if not self._credential_profile_key:
            raise PartyFlowPollingServiceError(
                error_code="partyflow_polling_invalid_credential_profile",
                reason="partyflow_polling_credential_profile_key is required",
            )
        if not self._account_id:
            raise PartyFlowPollingServiceError(
                error_code="partyflow_polling_invalid_account_id",
                reason="partyflow_polling_account_id is required",
            )

    async def start(self) -> None:
        """Start the background PartyFlow polling loop."""

        if self._task is not None:
            return
        self._bot_identity = await self._resolve_bot_identity()
        self._cursor = await self._load_cursor()
        await self._restore_pending_ingress_events()
        self._stop_event.clear()
        self._task = asyncio.create_task(
            self._poll_loop(),
            name=f"partyflow-polling:{self._endpoint.endpoint_id}",
        )

    async def stop(self) -> None:
        """Stop the background PartyFlow polling loop and flush buffered ingress."""

        task = self._task
        self._stop_event.set()
        if task is None:
            await self._cancel_pending_retry_tasks()
            return
        self._task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await self._cancel_pending_retry_tasks()
        await self._ingress_coalescer.flush_all()
        self._pending_restored = False

    async def _cancel_pending_retry_tasks(self) -> None:
        retry_tasks = tuple(self._pending_retry_tasks.values())
        self._pending_retry_tasks.clear()
        for retry_task in retry_tasks:
            retry_task.cancel()
        for retry_task in retry_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await retry_task

    async def poll_once(self) -> int:
        """Fetch one PartyFlow event page and process supported messages."""

        if self._bot_identity is None:
            self._bot_identity = await self._resolve_bot_identity()
        if not self._cursor:
            self._cursor = await self._load_cursor()
        await self._restore_pending_ingress_events()
        events, next_cursor = await self._fetch_events()
        for event in events:
            await self._process_polled_event(event)
        if next_cursor:
            self._cursor = next_cursor
            await self._persist_cursor(next_cursor)
        if self._task is None and self._endpoint.ingress_batch.enabled:
            await self._ingress_coalescer.flush_all()
        return len(events)

    async def probe_identity(self) -> PartyFlowBotIdentity:
        """Run a live PartyFlow `get_me` probe and return bot identity."""

        identity = await self._resolve_bot_identity()
        self._bot_identity = identity
        return identity

    async def reset_saved_cursor(self) -> bool:
        """Delete persisted PartyFlow polling cursor and reset in-memory state."""

        self._cursor = ""
        if not await asyncio.to_thread(self._state_path.exists):
            return False
        await asyncio.to_thread(self._state_path.unlink)
        return True

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                processed = await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("partyflow_polling_iteration_failed")
                await asyncio.sleep(self._settings.partyflow_polling_error_backoff_ms / 1000.0)
                continue
            if processed == 0:
                await asyncio.sleep(self._settings.partyflow_polling_idle_sleep_ms / 1000.0)

    async def _resolve_bot_identity(self) -> PartyFlowBotIdentity:
        if self._app_runtime is None:
            try:
                payload = await _get_me(
                    base_url=PARTYFLOW_API_BASE_URL,
                    token=await self._resolve_bot_token(),
                    timeout_sec=min(10, self._settings.tool_timeout_max_sec),
                )
            except (CredentialsServiceError, PartyFlowApiError) as exc:
                raise PartyFlowPollingServiceError(
                    error_code=getattr(exc, "error_code", "partyflow_get_me_failed"),
                    reason=getattr(exc, "reason", str(exc)),
                ) from exc
            return _parse_bot_identity(payload)

        result = await self._app_runtime.run(
            app="partyflow",
            action="get_me",
            ctx=self._app_context(timeout_sec=min(10, self._settings.tool_timeout_max_sec)),
            params={},
        )
        if not result.ok:
            raise PartyFlowPollingServiceError(
                error_code=result.error_code or "partyflow_get_me_failed",
                reason=result.reason or "PartyFlow get_me failed",
            )
        return _parse_bot_identity(result.payload)

    async def _fetch_events(self) -> tuple[list[Mapping[str, object]], str]:
        if self._app_runtime is None:
            try:
                payload = await _poll_events(
                    base_url=PARTYFLOW_API_BASE_URL,
                    token=await self._resolve_bot_token(),
                    cursor=self._cursor,
                    limit=self._settings.partyflow_polling_limit,
                    timeout_sec=min(30, self._settings.tool_timeout_max_sec),
                )
            except (CredentialsServiceError, PartyFlowApiError) as exc:
                raise PartyFlowPollingServiceError(
                    error_code=getattr(exc, "error_code", "partyflow_polling_fetch_failed"),
                    reason=getattr(exc, "reason", str(exc)),
                ) from exc
            return _parse_polled_events(payload)

        result = await self._app_runtime.run(
            app="partyflow",
            action="poll_events",
            ctx=self._app_context(timeout_sec=min(30, self._settings.tool_timeout_max_sec)),
            params={
                "cursor": self._cursor,
                "limit": self._settings.partyflow_polling_limit,
            },
        )
        if not result.ok:
            raise PartyFlowPollingServiceError(
                error_code=result.error_code or "partyflow_polling_fetch_failed",
                reason=result.reason or "PartyFlow event polling failed",
            )
        return _parse_polled_events(result.payload)

    async def _resolve_bot_token(self) -> str:
        return await get_credentials_service(self._settings).resolve_plaintext_for_app_tool(
            profile_id=self._endpoint.profile_id,
            tool_name="app.run",
            integration_name="partyflow",
            credential_profile_key=self._credential_profile_key,
            credential_name="partyflow_bot_token",
        )

    async def _process_polled_event(self, event: Mapping[str, object]) -> None:
        dedup_event_key = self._build_dedup_event_key(event)
        journal = get_channel_ingress_journal_service(self._settings)
        claimed = await journal.try_claim(
            endpoint_id=self._endpoint.endpoint_id,
            transport=self._endpoint.transport,
            event_key=dedup_event_key,
        )
        if not claimed:
            return
        try:
            payload = _payload_from_polled_event(event)
            ingress_event = self._build_ingress_event(
                payload=payload,
                dedup_event_key=dedup_event_key,
            )
            if ingress_event is None:
                return
            await self._ingress_coalescer.enqueue(ingress_event)
        except Exception:
            await journal.release_claim(
                endpoint_id=self._endpoint.endpoint_id,
                event_key=dedup_event_key,
            )
            raise

    def _build_ingress_event(
        self,
        *,
        dedup_event_key: str,
        payload: Mapping[str, object],
    ) -> ChannelIngressEvent | None:
        event_type = str(payload.get("event_type") or "").strip().upper()
        if event_type != "MESSAGE_CREATED":
            return None
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise PartyFlowPollingServiceError(
                error_code="partyflow_invalid_payload",
                reason="PartyFlow polling event is missing message payload_json",
            )
        message_id = (
            _coerce_optional_str(data.get("message_id"))
            or _coerce_optional_str(data.get("id"))
            or _coerce_optional_str(payload.get("event_id"))
        )
        conversation_id = _coerce_optional_str(data.get("conversation_id")) or _extract_identifier(
            data.get("conversation"), keys=("id", "conversation_id")
        )
        if not message_id or not conversation_id:
            raise PartyFlowPollingServiceError(
                error_code="partyflow_invalid_payload",
                reason="PartyFlow polling event is missing conversation_id or message_id",
            )
        author_id = _coerce_optional_str(data.get("author_id")) or _extract_identifier(
            data.get("author")
        )
        if self._bot_identity is not None and author_id == self._bot_identity.bot_id:
            return None
        chat_kind = _extract_chat_kind(data=data)
        if not is_channel_message_allowed(
            policy=self._endpoint.access_policy,
            chat_kind=chat_kind,
            peer_id=conversation_id,
            user_id=author_id,
        ):
            return None
        text = (
            _coerce_optional_str(data.get("text"))
            or _coerce_optional_str(data.get("content"))
            or _coerce_optional_str(data.get("message"))
            or ""
        )
        mentions = _extract_mentions(data.get("mentions")) or _extract_mentions(
            data.get("mentioned_users")
        )
        if not self._matches_trigger(text=text, mentions=mentions):
            return None
        if not text.strip():
            return None
        observed_at = (
            _coerce_optional_str(data.get("sent_at"))
            or _coerce_optional_str(payload.get("created_at"))
            or datetime.now(UTC).isoformat()
        )
        thread_id = _coerce_optional_str(data.get("thread_id")) or _coerce_optional_str(
            data.get("parent_message_id")
        )
        return ChannelIngressEvent(
            endpoint_id=self._endpoint.endpoint_id,
            transport=self._endpoint.transport,
            account_id=self._endpoint.account_id,
            peer_id=conversation_id,
            thread_id=thread_id,
            user_id=author_id,
            event_key=dedup_event_key,
            message_id=message_id,
            text=text.strip(),
            observed_at=observed_at,
            source_event_id=str(payload.get("event_id") or "").strip() or None,
            chat_kind=chat_kind,
        )

    def _matches_trigger(self, *, text: str, mentions: tuple[str, ...]) -> bool:
        if self._endpoint.trigger_mode == "all":
            return True
        if self._endpoint.trigger_mode == "mention":
            if self._bot_identity is None:
                return False
            if self._bot_identity.bot_id in set(mentions):
                return True
            return _matches_textual_bot_mention(text=text, identity=self._bot_identity)
        lowered = text.lower()
        return any(
            _matches_keyword_token(text=lowered, keyword=keyword)
            for keyword in self._endpoint.trigger_keywords
        )

    async def _flush_inbound_batch(self, batch: ChannelIngressBatch) -> None:
        selectors = RoutingSelectors(
            transport=self._endpoint.transport,
            account_id=batch.account_id,
            peer_id=batch.peer_id,
            thread_id=batch.thread_id,
            user_id=batch.user_id,
        )
        try:
            target = await resolve_runtime_target(
                settings=self._settings,
                explicit_profile_id=None,
                explicit_session_id=None,
                resolve_binding=True,
                selectors=selectors,
                default_profile_id=self._endpoint.profile_id,
                default_session_id=build_channel_default_session_id(selectors=selectors),
            )
        except ChannelBindingServiceError as exc:
            if exc.error_code != "channel_binding_no_match":
                raise
            return
        context_overrides = merge_turn_context_overrides(
            build_routing_context_overrides(target=target, selectors=selectors),
            build_active_channel_context_overrides(
                endpoint=self._endpoint,
                peer_id=batch.peer_id,
                thread_id=batch.thread_id,
                user_id=batch.user_id,
            ),
            build_ingress_batch_context_overrides(batch),
            build_channel_tool_profile_context_overrides(self._endpoint.tool_profile),
        )
        turn_result = await self._run_chat_turn(
            message=render_channel_ingress_batch_message(batch),
            profile_id=target.profile_id,
            session_id=target.session_id,
            client_msg_id=self._build_batch_client_msg_id(batch),
            context_overrides=context_overrides,
        )
        if self._endpoint.reply_mode != "same_conversation":
            return
        if turn_result.envelope.action != "finalize":
            return
        if should_suppress_channel_reply(turn_result.envelope):
            _LOGGER.warning(
                "partyflow_polling_suppressed_llm_error endpoint_id=%s run_id=%s",
                self._endpoint.endpoint_id,
                turn_result.run_id,
            )
            return
        response_text = turn_result.envelope.message.strip()
        if not response_text:
            return
        await self._channel_delivery_service.deliver_text(
            profile_id=turn_result.profile_id,
            session_id=turn_result.session_id,
            run_id=turn_result.run_id,
            target=ChannelDeliveryTarget(
                transport=self._endpoint.transport,
                account_id=batch.account_id,
                peer_id=batch.peer_id,
                thread_id=batch.thread_id,
                user_id=batch.user_id,
            ),
            text=response_text,
            credential_profile_key=self._endpoint.credential_profile_key,
        )

    async def _handle_ingress_batch_error(
        self,
        batch: ChannelIngressBatch,
        exc: Exception,
    ) -> None:
        retry_after_sec = _extract_retry_after_sec(exc)
        if retry_after_sec is None:
            retry_after_sec = 5
        retry_after_sec = max(0, retry_after_sec)
        self._schedule_pending_ingress_retry(batch=batch, delay_sec=float(retry_after_sec))
        _LOGGER.warning(
            "partyflow_polling_batch_retry_scheduled endpoint_id=%s peer_id=%s batch_size=%s retry_after_sec=%s error=%s",
            batch.endpoint_id,
            batch.peer_id,
            len(batch.events),
            retry_after_sec,
            f"{exc.__class__.__name__}: {exc}",
        )

    def _schedule_pending_ingress_retry(
        self,
        *,
        batch: ChannelIngressBatch,
        delay_sec: float,
    ) -> None:
        key = batch.conversation_key
        current_task = asyncio.current_task()
        existing = self._pending_retry_tasks.get(key)
        if existing is not None and not existing.done() and existing is not current_task:
            return
        task = asyncio.create_task(
            self._retry_pending_ingress_after_delay(batch=batch, delay_sec=delay_sec),
            name=f"partyflow-pending-retry:{batch.endpoint_id}:{batch.peer_id}",
        )
        self._pending_retry_tasks[key] = task
        task.add_done_callback(self._build_retry_task_done_callback(key))

    def _build_retry_task_done_callback(self, key: str) -> Callable[[asyncio.Task[None]], None]:
        def _done_callback(completed: asyncio.Task[None]) -> None:
            self._clear_retry_task(key, completed)

        return _done_callback

    def _clear_retry_task(self, key: str, completed: asyncio.Task[None]) -> None:
        if self._pending_retry_tasks.get(key) is completed:
            self._pending_retry_tasks.pop(key, None)

    async def _retry_pending_ingress_after_delay(
        self,
        *,
        batch: ChannelIngressBatch,
        delay_sec: float,
    ) -> None:
        try:
            if delay_sec > 0:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay_sec)
                    return
                except asyncio.TimeoutError:
                    pass
            if self._stop_event.is_set():
                return
            await self._ingress_coalescer.restore_pending(batch.events)
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(
                "partyflow_polling_pending_retry_failed endpoint_id=%s peer_id=%s conversation_key=%s",
                batch.endpoint_id,
                batch.peer_id,
                batch.conversation_key,
            )

    async def _persist_pending_ingress_event(self, event: ChannelIngressEvent) -> bool:
        return await get_channel_ingress_pending_service(self._settings).record_pending(event=event)

    async def _release_pending_ingress_batch(self, batch: ChannelIngressBatch) -> None:
        await get_channel_ingress_pending_service(self._settings).release_batch(batch=batch)

    async def _restore_pending_ingress_events(self) -> None:
        if self._pending_restored:
            return
        events = await get_channel_ingress_pending_service(self._settings).list_pending(
            endpoint_id=self._endpoint.endpoint_id
        )
        if events:
            await self._ingress_coalescer.restore_pending(tuple(events))
        self._pending_restored = True

    async def _load_cursor(self) -> str:
        return await asyncio.to_thread(
            load_partyflow_cursor,
            state_path=self._state_path,
            account_id=self._account_id,
        )

    async def _persist_cursor(self, cursor: str) -> None:
        await asyncio.to_thread(
            persist_partyflow_cursor,
            state_path=self._state_path,
            account_id=self._account_id,
            cursor=cursor,
        )

    def _app_context(self, *, timeout_sec: int) -> AppRuntimeContext:
        return AppRuntimeContext(
            profile_id=self._runtime_profile_id,
            session_id=_PARTYFLOW_SESSION_ID,
            run_id=0,
            credential_profile_key=self._credential_profile_key,
            timeout_sec=timeout_sec,
            approved_tool_names=CHANNEL_RUNTIME_APP_TOOL_GRANTS,
            approved_network_hosts=PARTYFLOW_CHANNEL_API_HOSTS,
        )

    @staticmethod
    def _build_dedup_event_key(event: Mapping[str, object]) -> str:
        stable_id = _coerce_optional_str(event.get("event_id")) or _coerce_optional_str(
            event.get("id")
        )
        if stable_id is not None:
            return "event:" + _normalize_partyflow_identifier(stable_id)
        fingerprint = hashlib.sha256(
            json.dumps(dict(event), ensure_ascii=True, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return "event:" + fingerprint

    @staticmethod
    def _build_batch_client_msg_id(batch: ChannelIngressBatch) -> str:
        if len(batch.events) == 1:
            return f"partyflow:{batch.account_id}:{batch.events[0].message_id}"
        first_id = batch.events[0].message_id
        last_id = batch.events[-1].message_id
        return (
            f"partyflow-batch:{batch.account_id}:{batch.peer_id}:{batch.thread_id or '-'}:"
            f"{batch.user_id or '-'}:{first_id}:{last_id}:{len(batch.events)}"
        )


def load_partyflow_cursor(*, state_path: Path, account_id: str) -> str:
    """Load persisted PartyFlow polling cursor when it belongs to the current account."""

    if not state_path.exists():
        return ""
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _LOGGER.warning("partyflow_polling_state_load_failed path=%s", state_path)
        return ""
    stored_account_id = payload.get("account_id")
    if (
        isinstance(stored_account_id, str)
        and stored_account_id.strip()
        and stored_account_id.strip() != account_id
    ):
        _LOGGER.info(
            "partyflow_polling_state_account_mismatch path=%s stored_account_id=%s current_account_id=%s",
            state_path,
            stored_account_id.strip(),
            account_id,
        )
        return ""
    cursor = payload.get("cursor")
    return cursor.strip() if isinstance(cursor, str) else ""


def persist_partyflow_cursor(*, state_path: Path, account_id: str, cursor: str) -> None:
    """Persist PartyFlow polling cursor atomically."""

    atomic_json_write(
        state_path,
        {"account_id": account_id, "cursor": cursor},
        mode=0o600,
    )


def _parse_bot_identity(payload: Mapping[str, object]) -> PartyFlowBotIdentity:
    bot = payload.get("bot")
    if not isinstance(bot, Mapping):
        raise PartyFlowPollingServiceError(
            error_code="partyflow_invalid_identity",
            reason="PartyFlow get_me returned invalid bot payload",
        )
    bot_id = str(bot.get("id") or "").strip()
    if not bot_id:
        raise PartyFlowPollingServiceError(
            error_code="partyflow_invalid_identity",
            reason="PartyFlow get_me returned empty bot id",
        )
    display_name = _coerce_optional_str(bot.get("display_name")) or _coerce_optional_str(
        bot.get("name")
    )
    return PartyFlowBotIdentity(bot_id=bot_id, display_name=display_name)


def _parse_polled_events(payload: Mapping[str, object]) -> tuple[list[Mapping[str, object]], str]:
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        return [], ""
    events = [item for item in raw_events if isinstance(item, Mapping)]
    next_cursor = str(payload.get("next_cursor") or "").strip()
    return events, next_cursor


def _payload_from_polled_event(event: Mapping[str, object]) -> Mapping[str, object]:
    payload_json = event.get("payload_json")
    if not isinstance(payload_json, str):
        raise PartyFlowPollingServiceError(
            error_code="partyflow_invalid_payload",
            reason="PartyFlow polling event payload_json must be a string",
        )
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise PartyFlowPollingServiceError(
            error_code="partyflow_invalid_payload",
            reason="PartyFlow polling event payload_json must be valid JSON",
        ) from exc
    if not isinstance(payload, Mapping):
        raise PartyFlowPollingServiceError(
            error_code="partyflow_invalid_payload",
            reason="PartyFlow polling event payload_json must be a JSON object",
        )
    return {
        "event_id": _coerce_optional_str(event.get("event_id")),
        "event_type": _coerce_optional_str(event.get("event_type")),
        "created_at": _coerce_optional_str(event.get("created_at")),
        "data": {str(key): value for key, value in payload.items()},
    }


def _coerce_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _extract_identifier(
    value: object, *, keys: tuple[str, ...] = ("id", "user_id", "bot_id")
) -> str | None:
    if not isinstance(value, Mapping):
        return _coerce_optional_str(value)
    for key in keys:
        candidate = _coerce_optional_str(value.get(key))
        if candidate is not None:
            return candidate
    return None


def _normalize_partyflow_identifier(value: str) -> str:
    lowered = value.lower()
    with contextlib.suppress(ValueError):
        return str(UUID(lowered))
    return value


def _extract_mentions(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in value:
        mention_id = _extract_identifier(raw, keys=("id", "user_id", "bot_id"))
        if mention_id is None or mention_id in seen:
            continue
        seen.add(mention_id)
        normalized.append(mention_id)
    return tuple(normalized)


def _extract_chat_kind(*, data: Mapping[str, object]) -> str | None:
    raw_kind = (
        _coerce_optional_str(data.get("conversation_type"))
        or _coerce_optional_str(data.get("chat_kind"))
        or _coerce_optional_str(data.get("type"))
    )
    if raw_kind is None:
        return "group"
    lowered = raw_kind.lower()
    if lowered in {"dm", "direct", "private"}:
        return "private"
    if lowered in {"group", "channel", "public", "supergroup"}:
        return lowered
    return lowered


def _extract_retry_after_sec(exc: Exception) -> int | None:
    if not isinstance(exc, ChannelDeliveryServiceError):
        return None
    retry_after = exc.metadata.get("retry_after_sec")
    if isinstance(retry_after, int):
        return retry_after
    if isinstance(retry_after, str) and retry_after.isdigit():
        return int(retry_after)
    return None


def _matches_keyword_token(*, text: str, keyword: str) -> bool:
    needle = keyword.strip().lower()
    if not needle:
        return False
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return False
        before_ok = index == 0 or not text[index - 1].isalnum()
        after_index = index + len(needle)
        after_ok = after_index >= len(text) or not text[after_index].isalnum()
        if before_ok and after_ok:
            return True
        start = index + 1


def _matches_textual_bot_mention(*, text: str, identity: PartyFlowBotIdentity) -> bool:
    display_name = identity.display_name
    if display_name is None:
        return False
    handle = display_name.strip().removeprefix("@").lower()
    if not handle or any(char.isspace() for char in handle):
        return False
    lowered = text.lower()
    needle = "@" + handle
    start = 0
    while True:
        index = lowered.find(needle, start)
        if index < 0:
            return False
        before_ok = index == 0 or not lowered[index - 1].isalnum()
        after_index = index + len(needle)
        after_ok = after_index >= len(lowered) or (
            not lowered[after_index].isalnum() and lowered[after_index] not in {"_", "-"}
        )
        if before_ok and after_ok:
            return True
        start = index + 1
