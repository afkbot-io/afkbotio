"""Outbound channel delivery runtime built on top of app integrations."""

from __future__ import annotations

import logging

from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.apps.runtime import AppRuntime
from afkbot.services.channel_routing.service import ChannelBindingService
from afkbot.services.channels.contracts import ChannelDeliveryResult, ChannelDeliveryTarget
from afkbot.services.channels.delivery_runtime import (
    ChannelDeliveryServiceError,
    ResolvedDeliveryTarget,
    build_app_runtime_context,
    resolve_delivery_target,
)
from afkbot.services.channels.delivery_telemetry import (
    ChannelDeliveryTelemetry,
    get_channel_delivery_diagnostics,
    get_channel_delivery_telemetry,
    reset_channel_delivery_diagnostics,
)
from afkbot.services.channels.sender_registry import (
    ChannelSenderRegistry,
    ChannelSenderRegistryError,
    get_channel_sender_registry,
)
from afkbot.services.telegram_text import split_telegram_text
from afkbot.services.tools.base import ToolResult
from afkbot.settings import Settings

__all__ = [
    "ChannelDeliveryService",
    "ChannelDeliveryServiceError",
    "get_channel_delivery_diagnostics",
    "reset_channel_delivery_diagnostics",
]

_LOGGER = logging.getLogger(__name__)
_TELEGRAM_ACTION_TIMEOUT_PREFIX = "Telegram action timed out after "


def _split_plain_text(*, text: str, limit: int) -> tuple[str, ...]:
    """Split plain text into bounded chunks without transport-specific formatting rules."""

    if len(text) <= limit:
        return (text,)

    normalized = text.strip()
    if not normalized:
        return ("",)

    chunks: list[str] = []
    remaining = normalized
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at <= 0:
            split_at = limit
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:limit]
            split_at = len(chunk)
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)


class ChannelDeliveryService:
    """Deliver finalized output to explicit external channel targets."""

    def __init__(
        self,
        settings: Settings,
        *,
        app_runtime: AppRuntime | None = None,
        binding_service: ChannelBindingService | None = None,
        sender_registry: ChannelSenderRegistry | None = None,
    ) -> None:
        self._settings = settings
        self._app_runtime = app_runtime or AppRuntime(settings)
        self._binding_service = binding_service
        self._sender_registry = sender_registry or get_channel_sender_registry(settings)
        self._telemetry: ChannelDeliveryTelemetry = get_channel_delivery_telemetry(settings)

    async def deliver_turn_result(
        self,
        *,
        turn_result: TurnResult | object,
        target: ChannelDeliveryTarget,
        credential_profile_key: str | None = None,
    ) -> ChannelDeliveryResult | None:
        """Deliver finalized assistant text when the completed turn emitted one."""

        if not isinstance(turn_result, TurnResult):
            return None
        if turn_result.envelope.action != "finalize":
            return None
        message = turn_result.envelope.message.strip()
        if not message:
            return None
        return await self.deliver_text(
            profile_id=turn_result.profile_id,
            session_id=turn_result.session_id,
            run_id=turn_result.run_id,
            target=target,
            text=message,
            credential_profile_key=credential_profile_key,
        )

    async def deliver_text(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        target: ChannelDeliveryTarget,
        text: str,
        credential_profile_key: str | None = None,
    ) -> ChannelDeliveryResult:
        """Deliver one text payload to supported channel target."""

        message = text.strip()
        event_target = target.model_dump(exclude_none=True)
        if not message:
            self._record_delivery_event(
                transport=target.transport,
                ok=False,
                error_code="channel_delivery_message_empty",
                target=event_target,
            )
            raise ChannelDeliveryServiceError(
                error_code="channel_delivery_message_empty",
                reason="Delivery message is empty.",
            )
        try:
            resolved = await resolve_delivery_target(
                settings=self._settings,
                target=target,
                binding_service=self._binding_service,
            )
            event_target = resolved.to_payload()
            chunk_payloads: list[dict[str, object]] = []
            message_chunks = self._split_message_for_transport(
                transport=resolved.transport,
                text=message,
            )
            for chunk in message_chunks:
                result = await self._deliver_via_transport(
                    profile_id=profile_id,
                    session_id=session_id,
                    run_id=run_id,
                    target=resolved,
                    text=chunk,
                    credential_profile_key=credential_profile_key,
                )
                if not result.ok:
                    raise ChannelDeliveryServiceError(
                        error_code=result.error_code or "channel_delivery_failed",
                        reason=result.reason or "Channel delivery failed",
                        metadata={
                            "target": event_target,
                            **result.metadata,
                        },
                    )
                chunk_payloads.append(result.payload)
            self._record_delivery_event(
                transport=resolved.transport,
                ok=True,
                error_code=None,
                target=event_target,
            )
            return ChannelDeliveryResult(
                transport=resolved.transport,
                target=event_target,
                payload=self._build_delivery_payload(
                    original_text=message,
                    chunk_payloads=chunk_payloads,
                ),
            )
        except ChannelDeliveryServiceError as exc:
            self._record_delivery_event(
                transport=target.transport,
                ok=False,
                error_code=exc.error_code,
                target=self._extract_event_target(target=target, metadata=exc.metadata),
            )
            raise
        except Exception as exc:
            self._record_delivery_event(
                transport=target.transport,
                ok=False,
                error_code="channel_delivery_failed",
                target=event_target,
            )
            raise ChannelDeliveryServiceError(
                error_code="channel_delivery_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
                metadata={"target": event_target},
            ) from exc

    async def _deliver_via_transport(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        target: ResolvedDeliveryTarget,
        text: str,
        credential_profile_key: str | None,
    ) -> ToolResult:
        if target.transport == "telegram":
            return await self._deliver_via_telegram(
                profile_id=profile_id,
                session_id=session_id,
                run_id=run_id,
                target=target,
                text=text,
                credential_profile_key=credential_profile_key,
            )
        if target.transport == "telegram_user":
            return await self._deliver_via_telegram_user(
                target=target,
                text=text,
            )
        if target.transport == "partyflow":
            return await self._deliver_via_partyflow(
                profile_id=profile_id,
                session_id=session_id,
                run_id=run_id,
                target=target,
                text=text,
                credential_profile_key=credential_profile_key,
            )
        return await self._deliver_via_smtp(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            target=target,
            text=text,
            credential_profile_key=credential_profile_key,
        )

    async def _deliver_via_telegram(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        target: ResolvedDeliveryTarget,
        text: str,
        credential_profile_key: str | None,
    ) -> ToolResult:
        params: dict[str, object] = {
            "text": text,
            "chat_id": target.peer_id,
        }
        if target.thread_id is not None:
            try:
                params["message_thread_id"] = int(target.thread_id)
            except ValueError as exc:
                metadata: dict[str, object] = dict(target.to_payload())
                raise ChannelDeliveryServiceError(
                    error_code="channel_delivery_invalid_thread_id",
                    reason=f"Invalid telegram thread id: {target.thread_id}",
                    metadata=metadata,
                ) from exc
        result = await self._run_telegram_send_message(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            credential_profile_key=credential_profile_key,
            params=params,
        )
        if result.ok or not self._is_soft_telegram_timeout_result(result):
            return result
        retry_timeout_sec = self._telegram_retry_timeout_sec()
        _LOGGER.warning(
            "telegram_delivery_timeout_retry profile_id=%s session_id=%s peer_id=%s retry_timeout_sec=%s",
            profile_id,
            session_id,
            target.peer_id,
            retry_timeout_sec,
        )
        return await self._run_telegram_send_message(
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            credential_profile_key=credential_profile_key,
            params=params,
            timeout_sec=retry_timeout_sec,
        )

    async def _deliver_via_telegram_user(
        self,
        *,
        target: ResolvedDeliveryTarget,
        text: str,
    ) -> ToolResult:
        account_id = target.account_id
        if account_id is None:
            missing_account_metadata: dict[str, object] = dict(target.to_payload())
            raise ChannelDeliveryServiceError(
                error_code="channel_delivery_target_incomplete",
                reason="Telegram user delivery target requires account_id.",
                metadata=missing_account_metadata,
            )
        try:
            sender = await self._sender_registry.get_sender(
                transport=target.transport,
                account_id=account_id,
            )
        except ChannelSenderRegistryError as exc:
            sender_lookup_metadata: dict[str, object] = dict(target.to_payload())
            raise ChannelDeliveryServiceError(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata=sender_lookup_metadata,
            ) from exc
        try:
            payload = await sender(target, text)
        except ChannelDeliveryServiceError:
            raise
        except Exception as exc:
            error_code = getattr(exc, "error_code", "channel_delivery_failed")
            reason = getattr(exc, "reason", f"{exc.__class__.__name__}: {exc}")
            delivery_error_metadata: dict[str, object] = dict(target.to_payload())
            extra_metadata = getattr(exc, "metadata", None)
            if isinstance(extra_metadata, dict):
                delivery_error_metadata = {
                    **delivery_error_metadata,
                    **extra_metadata,
                }
            raise ChannelDeliveryServiceError(
                error_code=str(error_code),
                reason=str(reason),
                metadata=delivery_error_metadata,
            ) from exc
        return ToolResult(ok=True, payload=payload)

    async def _deliver_via_smtp(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        target: ResolvedDeliveryTarget,
        text: str,
        credential_profile_key: str | None,
    ) -> ToolResult:
        return await self._app_runtime.run(
            app="smtp",
            action="send_email",
            ctx=build_app_runtime_context(
                settings=self._settings,
                profile_id=profile_id,
                session_id=session_id,
                run_id=run_id,
                credential_profile_key=credential_profile_key,
            ),
            params={
                "to_email": target.address,
                "subject": target.subject or "AFKBOT automation result",
                "body": text,
            },
        )

    async def _deliver_via_partyflow(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        target: ResolvedDeliveryTarget,
        text: str,
        credential_profile_key: str | None,
    ) -> ToolResult:
        return await self._app_runtime.run(
            app="partyflow",
            action="send_message",
            ctx=build_app_runtime_context(
                settings=self._settings,
                profile_id=profile_id,
                session_id=session_id,
                run_id=run_id,
                credential_profile_key=credential_profile_key,
            ),
            params={
                "conversation_id": target.peer_id,
                "content": text,
                "thread_id": target.thread_id,
            },
        )

    def _record_delivery_event(
        self,
        *,
        transport: str,
        ok: bool,
        error_code: str | None,
        target: dict[str, str],
    ) -> None:
        self._telemetry.record(
            transport=transport,
            ok=ok,
            error_code=error_code,
            target=target,
        )

    @staticmethod
    def _extract_event_target(
        *,
        target: ChannelDeliveryTarget,
        metadata: dict[str, object],
    ) -> dict[str, str]:
        candidate = metadata.get("target")
        if isinstance(candidate, dict):
            return {str(key): str(value) for key, value in candidate.items() if value is not None}
        return target.model_dump(exclude_none=True)

    @staticmethod
    def _split_message_for_transport(*, transport: str, text: str) -> tuple[str, ...]:
        if transport == "partyflow":
            return _split_plain_text(text=text, limit=4000)
        if transport not in {"telegram", "telegram_user"}:
            return (text,)
        return split_telegram_text(text)

    @staticmethod
    def _build_delivery_payload(
        *,
        original_text: str,
        chunk_payloads: list[dict[str, object]],
    ) -> dict[str, object]:
        if not chunk_payloads:
            return {}
        if len(chunk_payloads) == 1:
            return chunk_payloads[0]
        return {
            "chunk_count": len(chunk_payloads),
            "text_length": len(original_text),
            "chunks": chunk_payloads,
        }

    async def _run_telegram_send_message(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        credential_profile_key: str | None,
        params: dict[str, object],
        timeout_sec: int | None = None,
    ) -> ToolResult:
        """Run one Telegram send_message call with an optional override timeout."""

        ctx = build_app_runtime_context(
            settings=self._settings,
            profile_id=profile_id,
            session_id=session_id,
            run_id=run_id,
            credential_profile_key=credential_profile_key,
        )
        if timeout_sec is not None:
            ctx = AppRuntimeContext(
                profile_id=ctx.profile_id,
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                credential_profile_key=ctx.credential_profile_key,
                timeout_sec=timeout_sec,
            )
        return await self._app_runtime.run(
            app="telegram",
            action="send_message",
            ctx=ctx,
            params=params,
        )

    def _telegram_retry_timeout_sec(self) -> int:
        """Return one bounded retry timeout for Telegram Bot API sends."""

        default_timeout = max(1, self._settings.tool_timeout_default_sec)
        return min(self._settings.tool_timeout_max_sec, max(default_timeout * 2, 30))

    @staticmethod
    def _is_soft_telegram_timeout_result(result: ToolResult) -> bool:
        """Return whether one Telegram app failure is a transport timeout."""

        if result.ok:
            return False
        if result.error_code != "app_run_failed":
            return False
        if not isinstance(result.reason, str):
            return False
        return result.reason.startswith(_TELEGRAM_ACTION_TIMEOUT_PREFIX)
