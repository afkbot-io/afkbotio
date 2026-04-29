"""PartyFlow outgoing-webhook channel runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import logging
import time
from uuid import UUID
from typing import Any

from afkbot.services.agent_loop.api_runtime import run_chat_turn
from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.runtime import AppRuntime
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    build_routing_context_overrides,
    resolve_runtime_target,
)
from afkbot.services.channel_routing.service import ChannelBindingServiceError
from afkbot.services.channels.access_policy import is_channel_message_allowed
from afkbot.services.channels.context_overrides import build_channel_tool_profile_context_overrides
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError
from afkbot.services.channels.endpoint_contracts import PartyFlowWebhookEndpointConfig
from afkbot.services.channels.ingress_coalescer import (
    ChannelIngressBatch,
    ChannelIngressCoalescer,
    ChannelIngressEvent,
    build_ingress_batch_context_overrides,
    render_channel_ingress_batch_message,
)
from afkbot.services.channels.ingress_journal import get_channel_ingress_journal_service
from afkbot.services.channels.ingress_persistence import get_channel_ingress_pending_service
from afkbot.services.channels.partyflow_runtime_registry import (
    get_partyflow_webhook_runtime_registry,
)
from afkbot.services.channels.reply_policy import should_suppress_channel_reply
from afkbot.services.channels.service import ChannelDeliveryService
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.settings import Settings

_LOGGER = logging.getLogger(__name__)
_PARTYFLOW_WEBHOOK_SIGNING_SECRET = "partyflow_webhook_signing_secret"
_PARTYFLOW_SESSION_ID = "partyflow-webhook"
_PARTYFLOW_SIGNATURE_WINDOW_SEC = 3600


class PartyFlowWebhookServiceError(ValueError):
    """Structured PartyFlow webhook runtime failure."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _QueuedWebhookEvent:
    """Normalized PartyFlow webhook event queued for background processing."""

    ingress_event: ChannelIngressEvent


class PartyFlowWebhookService:
    """Accept verified PartyFlow webhook deliveries and route them through AgentLoop."""

    def __init__(
        self,
        settings: Settings,
        *,
        endpoint: PartyFlowWebhookEndpointConfig,
        app_runtime: AppRuntime | None = None,
        channel_delivery_service: ChannelDeliveryService | None = None,
        run_chat_turn_fn: Any = run_chat_turn,
    ) -> None:
        self._settings = settings
        self._endpoint = PartyFlowWebhookEndpointConfig.model_validate(endpoint.model_dump())
        self._app_runtime = app_runtime or AppRuntime(settings)
        self._channel_delivery_service = channel_delivery_service or ChannelDeliveryService(
            settings
        )
        self._run_chat_turn = run_chat_turn_fn
        self._queue: asyncio.Queue[_QueuedWebhookEvent] = asyncio.Queue(
            maxsize=settings.runtime_queue_max_size
        )
        self._worker_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._ingress_coalescer = ChannelIngressCoalescer(
            config=self._endpoint.ingress_batch,
            on_flush=self._flush_inbound_batch,
            on_flush_error=self._handle_ingress_batch_error,
            release_batch=self._release_pending_ingress_batch,
        )
        self._pending_restored = False
        self._retry_task: asyncio.Task[None] | None = None
        self._retry_deadline: datetime | None = None
        self._retry_lock = asyncio.Lock()
        self._bot_id: str | None = None
        self._signing_secret: bytes | None = None

    async def start(self) -> None:
        """Start webhook intake worker and register runtime for API dispatch."""

        if self._worker_task is not None:
            return
        await self._bootstrap_identity()
        await self._restore_pending_ingress_events()
        self._stop_event.clear()
        await get_partyflow_webhook_runtime_registry(self._settings).register(
            endpoint_id=self._endpoint.endpoint_id,
            service=self,
        )
        self._worker_task = asyncio.create_task(
            self._worker_loop(),
            name=f"partyflow-webhook:{self._endpoint.endpoint_id}",
        )

    async def stop(self) -> None:
        """Stop webhook intake worker and unregister runtime dispatch."""

        await get_partyflow_webhook_runtime_registry(self._settings).unregister(
            endpoint_id=self._endpoint.endpoint_id,
            service=self,
        )
        self._stop_event.set()
        worker_task = self._worker_task
        self._worker_task = None
        retry_task = self._retry_task
        self._retry_task = None
        self._retry_deadline = None
        if retry_task is not None:
            retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retry_task
        if worker_task is not None:
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        await self._ingress_coalescer.flush_all()
        self._pending_restored = False

    async def handle_webhook(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, object]]:
        """Verify and enqueue one PartyFlow outgoing webhook delivery."""

        delivery_id = headers.get("x-partyflow-delivery-id", "").strip() or None
        if delivery_id is None:
            return 400, {
                "ok": False,
                "error_code": "partyflow_missing_delivery_id",
                "reason": "Missing X-PartyFlow-Delivery-Id header",
            }
        verified = self._verify_signature(headers=headers, body=body)
        if not verified:
            return 401, {
                "ok": False,
                "error_code": "partyflow_invalid_signature",
                "reason": "Invalid PartyFlow webhook signature",
            }
        try:
            payload = _parse_payload(body)
        except PartyFlowWebhookServiceError as exc:
            return 400, {"ok": False, "error_code": exc.error_code, "reason": exc.reason}

        dedup_event_key = self._build_dedup_event_key(payload=payload, headers=headers, body=body)
        journal = get_channel_ingress_journal_service(self._settings)
        claimed = await journal.try_claim(
            endpoint_id=self._endpoint.endpoint_id,
            transport=self._endpoint.transport,
            event_key=dedup_event_key,
        )
        if not claimed:
            _LOGGER.debug(
                "partyflow_webhook_duplicate endpoint_id=%s event_key=%s delivery_id=%s",
                self._endpoint.endpoint_id,
                dedup_event_key,
                delivery_id,
            )
            return 200, {"ok": True, "duplicate": True}

        try:
            ingress_event = self._build_ingress_event(
                payload=payload,
                dedup_event_key=dedup_event_key,
            )
            if ingress_event is None:
                await journal.record_processed(
                    endpoint_id=self._endpoint.endpoint_id,
                    transport=self._endpoint.transport,
                    event_key=dedup_event_key,
                )
                return 200, {"ok": True, "ignored": True}
            persisted = await get_channel_ingress_pending_service(self._settings).record_pending(
                event=ingress_event
            )
            if not persisted:
                await self._schedule_pending_retry(retry_after_sec=1)
                return 200, {"ok": True, "duplicate": True}
            try:
                self._queue.put_nowait(_QueuedWebhookEvent(ingress_event=ingress_event))
            except asyncio.QueueFull:
                await get_channel_ingress_pending_service(self._settings).release_event(
                    endpoint_id=self._endpoint.endpoint_id,
                    event_key=ingress_event.event_key,
                )
                await journal.release_claim(
                    endpoint_id=self._endpoint.endpoint_id,
                    event_key=dedup_event_key,
                )
                return 429, {
                    "ok": False,
                    "error_code": "partyflow_queue_full",
                    "reason": "PartyFlow webhook queue is full",
                    "retry_after": 1,
                }
        except PartyFlowWebhookServiceError as exc:
            await journal.release_claim(
                endpoint_id=self._endpoint.endpoint_id,
                event_key=dedup_event_key,
            )
            return 400, {"ok": False, "error_code": exc.error_code, "reason": exc.reason}
        except Exception:
            await journal.release_claim(
                endpoint_id=self._endpoint.endpoint_id,
                event_key=dedup_event_key,
            )
            raise
        return 202, {"accepted": True}

    def _build_dedup_event_key(
        self,
        *,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
        body: bytes,
    ) -> str:
        stable_id = _extract_partyflow_event_identifier(payload)
        if stable_id is not None:
            return "event:" + hashlib.sha256(stable_id.encode("utf-8")).hexdigest()
        timestamp = headers.get("x-partyflow-timestamp", "").strip()
        material = timestamp.encode("utf-8") + b":" + body
        return "signed-payload:" + hashlib.sha256(material).hexdigest()

    async def _bootstrap_identity(self) -> None:
        """Resolve signing secret and bot identity before the service starts."""

        credentials = get_credentials_service(self._settings)
        try:
            secret = await credentials.resolve_plaintext_for_app_tool(
                profile_id=self._endpoint.profile_id,
                tool_name="app.run",
                integration_name="partyflow",
                credential_profile_key=self._endpoint.credential_profile_key,
                credential_name=_PARTYFLOW_WEBHOOK_SIGNING_SECRET,
            )
        except CredentialsServiceError as exc:
            raise PartyFlowWebhookServiceError(
                error_code=exc.error_code,
                reason=exc.reason,
            ) from exc
        self._signing_secret = secret.encode("utf-8")
        result = await self._app_runtime.run(
            app="partyflow",
            action="get_me",
            ctx=self._app_context(timeout_sec=min(10, self._settings.tool_timeout_max_sec)),
            params={},
        )
        if not result.ok:
            raise PartyFlowWebhookServiceError(
                error_code=result.error_code or "partyflow_get_me_failed",
                reason=result.reason or "PartyFlow get_me failed",
            )
        payload = result.payload.get("bot")
        if not isinstance(payload, Mapping):
            raise PartyFlowWebhookServiceError(
                error_code="partyflow_invalid_identity",
                reason="PartyFlow get_me returned invalid bot payload",
            )
        bot_id = str(payload.get("id") or "").strip()
        if not bot_id:
            raise PartyFlowWebhookServiceError(
                error_code="partyflow_invalid_identity",
                reason="PartyFlow get_me returned empty bot id",
            )
        self._bot_id = bot_id

    async def _worker_loop(self) -> None:
        """Drain accepted webhook events through the shared ingress coalescer."""

        while not self._stop_event.is_set():
            item = await self._queue.get()
            try:
                await self._ingress_coalescer.enqueue(item.ingress_event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                retry_after_sec = _extract_retry_after_sec(exc) or 5
                await self._schedule_pending_retry(retry_after_sec=retry_after_sec)
                _LOGGER.exception(
                    "partyflow_webhook_event_failed endpoint_id=%s event_key=%s retry_after_sec=%s error=%s",
                    self._endpoint.endpoint_id,
                    item.ingress_event.event_key,
                    retry_after_sec,
                    f"{exc.__class__.__name__}: {exc}",
                )
            finally:
                self._queue.task_done()

    async def _flush_inbound_batch(self, batch: ChannelIngressBatch) -> None:
        """Flush one coalesced PartyFlow ingress batch through AgentLoop and reply delivery."""

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
                default_session_id=f"partyflow:{batch.peer_id}",
            )
        except ChannelBindingServiceError as exc:
            if exc.error_code != "channel_binding_no_match":
                raise
            return
        context_overrides = merge_turn_context_overrides(
            build_routing_context_overrides(target=target, selectors=selectors),
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
                "partyflow_suppressed_llm_error endpoint_id=%s run_id=%s",
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
        self._clear_retry_state()

    async def _handle_ingress_batch_error(
        self,
        batch: ChannelIngressBatch,
        exc: Exception,
    ) -> None:
        """Schedule deferred retries for failed PartyFlow ingress batches."""

        retry_after_sec = _extract_retry_after_sec(exc)
        if retry_after_sec is None:
            retry_after_sec = 5
        await self._schedule_pending_retry(retry_after_sec=retry_after_sec)
        _LOGGER.warning(
            "partyflow_batch_retry_scheduled endpoint_id=%s peer_id=%s batch_size=%s retry_after_sec=%s error=%s",
            batch.endpoint_id,
            batch.peer_id,
            len(batch.events),
            retry_after_sec,
            f"{exc.__class__.__name__}: {exc}",
        )

    async def _release_pending_ingress_batch(self, batch: ChannelIngressBatch) -> None:
        """Release persisted ingress state for one successfully processed batch."""

        await get_channel_ingress_pending_service(self._settings).release_batch(batch=batch)

    async def _restore_pending_ingress_events(self) -> None:
        """Restore persisted pending events once for the current runtime session."""

        if self._pending_restored:
            return
        events = await get_channel_ingress_pending_service(self._settings).list_pending(
            endpoint_id=self._endpoint.endpoint_id
        )
        if events:
            await self._ingress_coalescer.restore_pending(tuple(events))
        self._pending_restored = True

    async def _schedule_pending_retry(self, *, retry_after_sec: int) -> None:
        """Schedule one deferred retry for persisted pending ingress events."""

        delay_sec = max(1, int(retry_after_sec))
        deadline = datetime.now(UTC) + timedelta(seconds=delay_sec)
        task_to_cancel: asyncio.Task[None] | None = None
        async with self._retry_lock:
            effective_deadline = deadline
            if self._retry_deadline is not None and self._retry_deadline > effective_deadline:
                effective_deadline = self._retry_deadline
            if (
                self._retry_task is not None
                and not self._retry_task.done()
                and self._retry_deadline == effective_deadline
            ):
                return
            self._retry_deadline = effective_deadline
            if self._retry_task is not None and not self._retry_task.done():
                task_to_cancel = self._retry_task
            self._retry_task = asyncio.create_task(
                self._retry_pending_after_deadline(deadline=effective_deadline),
                name=f"partyflow-ingress-retry:{self._endpoint.endpoint_id}",
            )
        if task_to_cancel is not None:
            task_to_cancel.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task_to_cancel

    async def _retry_pending_after_deadline(self, *, deadline: datetime) -> None:
        """Restore persisted pending events after the computed retry deadline."""

        current_task = asyncio.current_task()
        delay_sec = max((deadline - datetime.now(UTC)).total_seconds(), 0.0)
        try:
            if delay_sec > 0:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay_sec)
                    return
                except asyncio.TimeoutError:
                    pass
            if self._stop_event.is_set():
                return
            events = await get_channel_ingress_pending_service(self._settings).list_pending(
                endpoint_id=self._endpoint.endpoint_id
            )
            if events:
                await self._ingress_coalescer.restore_pending(tuple(events))
        finally:
            async with self._retry_lock:
                if self._retry_task is current_task:
                    self._retry_task = None
                    if self._retry_deadline == deadline:
                        self._retry_deadline = None

    def _clear_retry_state(self) -> None:
        self._retry_deadline = None

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
            raise PartyFlowWebhookServiceError(
                error_code="partyflow_invalid_payload",
                reason="PartyFlow payload is missing message data",
            )
        message_id = (
            _coerce_optional_str(data.get("message_id"))
            or _coerce_optional_str(data.get("id"))
            or _coerce_optional_str(payload.get("message_id"))
            or _coerce_optional_str(payload.get("event_id"))
        )
        conversation_id = (
            _coerce_optional_str(payload.get("conversation_id"))
            or _coerce_optional_str(data.get("conversation_id"))
            or _extract_identifier(data.get("conversation"), keys=("id", "conversation_id"))
        )
        if not message_id or not conversation_id:
            raise PartyFlowWebhookServiceError(
                error_code="partyflow_invalid_payload",
                reason="PartyFlow payload is missing conversation_id or message_id",
            )
        author_id = (
            _coerce_optional_str(data.get("author_id"))
            or _coerce_optional_str(payload.get("actor_user_id"))
            or _extract_identifier(data.get("author"))
            or _extract_identifier(payload.get("actor"), keys=("id", "user_id"))
        )
        if self._bot_id is not None and author_id == self._bot_id:
            return None
        chat_kind = _extract_chat_kind(payload=payload, data=data)
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
        rendered_text = _render_partyflow_ingress_text(
            text=text,
            payload=payload,
            include_context=self._endpoint.include_context,
        )
        if not rendered_text.strip():
            return None
        occurred_at = (
            _coerce_optional_str(payload.get("occurred_at"))
            or _coerce_optional_str(data.get("occurred_at"))
            or _coerce_optional_str(data.get("created_at"))
            or datetime.now(UTC).isoformat()
        )
        thread_id = (
            _coerce_optional_str(payload.get("thread_id"))
            or _coerce_optional_str(data.get("thread_id"))
            or _extract_identifier(data.get("thread"), keys=("id", "thread_id"))
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
            text=rendered_text,
            observed_at=occurred_at,
            source_event_id=str(payload.get("event_id") or "").strip() or None,
            chat_kind=chat_kind,
        )

    def _matches_trigger(self, *, text: str, mentions: tuple[str, ...]) -> bool:
        if self._endpoint.trigger_mode == "all":
            return True
        if self._endpoint.trigger_mode == "mention":
            return self._bot_id is not None and self._bot_id in set(mentions)
        lowered = text.lower()
        return any(
            _matches_keyword_token(text=lowered, keyword=keyword)
            for keyword in self._endpoint.trigger_keywords
        )

    def _verify_signature(self, *, headers: Mapping[str, str], body: bytes) -> bool:
        signing_secret = self._signing_secret
        if signing_secret is None:
            return False
        timestamp = headers.get("x-partyflow-timestamp", "").strip()
        signature = headers.get("x-partyflow-signature", "").strip()
        if not timestamp or not signature:
            return False
        try:
            ts_value = int(timestamp)
        except ValueError:
            return False
        now_ts = int(time.time())
        if abs(now_ts - ts_value) > _PARTYFLOW_SIGNATURE_WINDOW_SEC:
            return False
        expected_v1 = (
            "sha256="
            + hmac.new(
                signing_secret,
                f"v1:{timestamp}:".encode("utf-8") + body,
                hashlib.sha256,
            ).hexdigest()
        )
        expected_legacy = (
            "sha256="
            + hmac.new(
                signing_secret,
                f"{timestamp}.".encode("utf-8") + body,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(signature, expected_v1) or hmac.compare_digest(
            signature, expected_legacy
        )

    def _app_context(self, *, timeout_sec: int) -> AppRuntimeContext:
        return AppRuntimeContext(
            profile_id=self._endpoint.profile_id,
            session_id=_PARTYFLOW_SESSION_ID,
            run_id=0,
            credential_profile_key=self._endpoint.credential_profile_key,
            timeout_sec=timeout_sec,
        )

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


def _parse_payload(body: bytes) -> Mapping[str, object]:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PartyFlowWebhookServiceError(
            error_code="partyflow_invalid_payload",
            reason="PartyFlow payload must be a valid JSON object",
        ) from exc
    if not isinstance(parsed, dict):
        raise PartyFlowWebhookServiceError(
            error_code="partyflow_invalid_payload",
            reason="PartyFlow payload must be a JSON object",
        )
    return {str(key): value for key, value in parsed.items()}


def _render_partyflow_ingress_text(
    *,
    text: str,
    payload: Mapping[str, object],
    include_context: bool,
) -> str:
    content = text.strip()
    if not include_context:
        return content
    messages = _extract_context_messages(payload)
    if not messages:
        return content
    parts: list[str] = []
    if content:
        parts.append(content)
        parts.append("")
    parts.append("Recent PartyFlow context before this message:")
    for index, raw in enumerate(messages, start=1):
        parts.extend(
            [
                f"[ctx {index}] sender_id: {_extract_identifier(raw.get('author')) or _coerce_optional_str(raw.get('author_id')) or '-'}",
                f"message_id: {_coerce_optional_str(raw.get('id')) or _coerce_optional_str(raw.get('message_id')) or '-'}",
                f"observed_at: {_coerce_optional_str(raw.get('created_at')) or _coerce_optional_str(raw.get('occurred_at')) or '-'}",
                _coerce_optional_str(raw.get("content"))
                or _coerce_optional_str(raw.get("text"))
                or _coerce_optional_str(raw.get("message"))
                or "",
                "",
            ]
        )
    return "\n".join(parts).strip()


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


def _extract_partyflow_event_identifier(payload: Mapping[str, object]) -> str | None:
    """Return a stable PartyFlow event/message identifier from signed payload fields."""

    data_raw = payload.get("data")
    data = data_raw if isinstance(data_raw, Mapping) else {}
    candidates: tuple[object, ...] = (
        payload.get("event_id"),
        data.get("event_id"),
        data.get("message_id"),
        payload.get("message_id"),
    )
    for candidate in candidates:
        value = _coerce_optional_str(candidate)
        if value is not None:
            return _normalize_partyflow_identifier(value)
    return None


def _normalize_partyflow_identifier(value: str) -> str:
    """Normalize identifiers so UUID-like values dedupe independent of casing."""

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


def _extract_context_messages(payload: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    context = payload.get("context")
    if isinstance(context, Mapping):
        messages = context.get("messages") or context.get("context_messages")
        if isinstance(messages, list):
            return tuple(item for item in messages if isinstance(item, Mapping))
    if isinstance(context, list):
        return tuple(item for item in context if isinstance(item, Mapping))
    raw_messages = payload.get("context_messages")
    if isinstance(raw_messages, list):
        return tuple(item for item in raw_messages if isinstance(item, Mapping))
    return ()


def _extract_chat_kind(*, payload: Mapping[str, object], data: Mapping[str, object]) -> str | None:
    raw_kind = (
        _coerce_optional_str(payload.get("conversation_type"))
        or _coerce_optional_str(data.get("conversation_type"))
        or _coerce_optional_str(payload.get("chat_kind"))
        or _coerce_optional_str(data.get("chat_kind"))
        or _coerce_optional_str(data.get("type"))
    )
    if raw_kind is None:
        return None
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
