"""Tool plugin for outbound channel delivery."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError
from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointService,
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
)
from afkbot.services.channels.service import ChannelDeliveryService
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings

_SUPPORTED_CHANNEL_SEND_TRANSPORTS = {"telegram", "telegram_user"}


class ChannelSendParams(ToolParameters):
    """Parameters for channel.send."""

    transport: str = Field(min_length=1, max_length=64)
    text: str = Field(default="", max_length=200000)
    endpoint_id: str | None = Field(default=None, min_length=1, max_length=120)
    binding_id: str | None = Field(default=None, min_length=1, max_length=255)
    account_id: str | None = Field(default=None, min_length=1, max_length=255)
    peer_id: str | None = Field(default=None, min_length=1, max_length=255)
    chat_id: str | None = Field(default=None, min_length=1, max_length=255)
    thread_id: str | None = Field(default=None, min_length=1, max_length=255)
    user_id: str | None = Field(default=None, min_length=1, max_length=255)
    address: str | None = Field(default=None, min_length=1, max_length=255)
    subject: str | None = Field(default=None, min_length=1, max_length=255)
    credential_profile_key: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def _normalize_aliases(self) -> "ChannelSendParams":
        transport = self.transport.strip().lower()
        self.transport = transport
        if self.peer_id is None and self.chat_id is not None and transport in {"telegram", "telegram_user"}:
            self.peer_id = self.chat_id
        return self


class ChannelSendTool(ToolBase):
    """Send text back through a configured external channel."""

    name = "channel.send"
    description = (
        "Send a text message through a configured channel. "
        "Use transport=telegram for Bot API channels and transport=telegram_user for Telethon userbot channels. "
        "Telegram targets need endpoint_id plus binding_id or peer_id/chat_id; telegram_user also needs account_id unless endpoint_id/binding_id supplies it. "
        "Optional thread_id targets forum topics. Optional credential_profile_key selects the sender credential profile."
    )
    parameters_model = ChannelSendParams

    def __init__(
        self,
        settings: Settings,
        *,
        delivery_service: ChannelDeliveryService | None = None,
        endpoint_service: ChannelEndpointService | None = None,
    ) -> None:
        self._settings = settings
        self._delivery_service = delivery_service or ChannelDeliveryService(settings)
        self._endpoint_service = endpoint_service or get_channel_endpoint_service(settings)

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, ChannelSendParams) else ChannelSendParams.model_validate(params.model_dump())
        scope_error = self._ensure_profile_scope(ctx=ctx, payload=payload)
        if scope_error is not None:
            return scope_error
        message = payload.text.strip()
        if not message:
            return ToolResult.error(
                error_code="channel_send_text_required",
                reason="channel.send requires non-empty text.",
            )
        if payload.transport not in _SUPPORTED_CHANNEL_SEND_TRANSPORTS:
            return ToolResult.error(
                error_code="channel_send_transport_not_supported",
                reason="channel.send supports only telegram and telegram_user transports.",
                metadata={"transport": payload.transport},
            )
        try:
            target = ChannelDeliveryTarget(
                transport=payload.transport,
                binding_id=payload.binding_id,
                account_id=payload.account_id,
                peer_id=payload.peer_id,
                thread_id=payload.thread_id,
                user_id=payload.user_id,
                address=payload.address,
                subject=payload.subject,
            )
            endpoint_or_error = await self._resolve_endpoint_for_outbound_policy(
                ctx=ctx,
                payload=payload,
                target=target,
            )
            if isinstance(endpoint_or_error, ToolResult):
                return endpoint_or_error
            outbound_policy_error = self._validate_outbound_policy(
                endpoint=endpoint_or_error,
                target=target,
            )
            if outbound_policy_error is not None:
                return outbound_policy_error
            target = target.model_copy(
                update={"account_id": target.account_id or endpoint_or_error.account_id}
            )
            result = await self._delivery_service.deliver_text(
                profile_id=ctx.profile_id,
                session_id=ctx.session_id,
                run_id=ctx.run_id,
                target=target,
                text=message,
                credential_profile_key=payload.credential_profile_key
                or endpoint_or_error.credential_profile_key,
            )
        except ChannelDeliveryServiceError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata=exc.metadata,
            )
        except ValueError as exc:
            return ToolResult.error(
                error_code="channel_send_target_invalid",
                reason=str(exc),
            )
        return ToolResult(ok=True, payload=_delivery_result_payload(result))

    def _validate_outbound_policy(
        self,
        *,
        endpoint: ChannelEndpointConfig,
        target: ChannelDeliveryTarget,
    ) -> ToolResult | None:
        allow_to = endpoint.access_policy.outbound_allow_to
        if not allow_to:
            return None
        if "*" in allow_to:
            return None
        peer_id = (target.peer_id or "").strip()
        if peer_id and peer_id in allow_to:
            return None
        return ToolResult.error(
            error_code="channel_send_target_not_allowed",
            reason=(
                "channel.send target is not allowed by the endpoint outbound allowlist. "
                "Use an allowed peer_id/chat_id or update the channel access policy."
            ),
            metadata={
                "endpoint_id": endpoint.endpoint_id,
                "transport": target.transport,
                "peer_id": peer_id,
            },
        )

    async def _resolve_endpoint_for_outbound_policy(
        self,
        *,
        ctx: ToolContext,
        payload: ChannelSendParams,
        target: ChannelDeliveryTarget,
    ) -> ChannelEndpointConfig | ToolResult:
        endpoint_id = payload.endpoint_id or await self._endpoint_id_from_binding_id(payload.binding_id)
        if endpoint_id is not None:
            try:
                endpoint = await self._endpoint_service.get(endpoint_id=endpoint_id)
            except ChannelEndpointServiceError as exc:
                return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
            return self._validate_endpoint_matches_context(
                endpoint=endpoint,
                ctx=ctx,
                target=target,
            )
        endpoints = await self._endpoint_service.list(
            transport=target.transport,
            enabled=True,
            profile_id=ctx.profile_id,
        )
        candidates = [
            endpoint
            for endpoint in endpoints
            if target.account_id is None or endpoint.account_id == target.account_id
        ]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            return ToolResult.error(
                error_code="channel_send_endpoint_required",
                reason=(
                    "channel.send requires endpoint_id or a binding_id derived from a channel endpoint "
                    "when no matching enabled endpoint can be inferred."
                ),
                metadata={"transport": target.transport, "account_id": target.account_id},
            )
        return ToolResult.error(
            error_code="channel_send_endpoint_ambiguous",
            reason=(
                "channel.send matched multiple enabled endpoints. Pass endpoint_id so outbound "
                "allowlist checks are evaluated against the intended channel."
            ),
            metadata={
                "transport": target.transport,
                "account_id": target.account_id,
                "endpoint_ids": [item.endpoint_id for item in candidates],
            },
        )

    async def _endpoint_id_from_binding_id(self, binding_id: str | None) -> str | None:
        if binding_id is None:
            return None
        normalized = binding_id.strip()
        if not normalized:
            return None
        candidates = (normalized, normalized.split(":", 1)[0])
        for candidate in candidates:
            try:
                endpoint = await self._endpoint_service.get(endpoint_id=candidate)
            except ChannelEndpointServiceError:
                continue
            return endpoint.endpoint_id
        return None

    @staticmethod
    def _validate_endpoint_matches_context(
        *,
        endpoint: ChannelEndpointConfig,
        ctx: ToolContext,
        target: ChannelDeliveryTarget,
    ) -> ChannelEndpointConfig | ToolResult:
        if endpoint.profile_id != ctx.profile_id:
            return ToolResult.error(
                error_code="channel_send_endpoint_not_in_profile",
                reason="channel.send endpoint does not belong to the active profile.",
                metadata={"endpoint_id": endpoint.endpoint_id, "profile_id": endpoint.profile_id},
            )
        if endpoint.transport != target.transport:
            return ToolResult.error(
                error_code="channel_send_endpoint_transport_mismatch",
                reason="channel.send transport does not match the selected endpoint.",
                metadata={
                    "endpoint_id": endpoint.endpoint_id,
                    "endpoint_transport": endpoint.transport,
                    "target_transport": target.transport,
                },
            )
        if target.account_id is not None and endpoint.account_id != target.account_id:
            return ToolResult.error(
                error_code="channel_send_endpoint_account_mismatch",
                reason="channel.send account_id does not match the selected endpoint.",
                metadata={
                    "endpoint_id": endpoint.endpoint_id,
                    "endpoint_account_id": endpoint.account_id,
                    "target_account_id": target.account_id,
                },
            )
        return endpoint


def create_tool(settings: Settings) -> ToolBase:
    """Create channel.send tool instance."""

    return ChannelSendTool(settings=settings)


def _delivery_result_payload(result: object) -> dict[str, object]:
    """Normalize service return values from tests and real delivery runtime."""

    if hasattr(result, "model_dump"):
        dumped = result.model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    if isinstance(result, dict):
        return {str(key): _jsonish(value) for key, value in result.items()}
    return {"result": _jsonish(result)}


def _jsonish(value: Any) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonish(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonish(item) for item in value]
    return str(value)
