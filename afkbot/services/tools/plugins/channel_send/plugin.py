"""Tool plugin for outbound channel delivery."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from afkbot.services.channel_routing import ChannelBindingRule
from afkbot.services.channel_routing.service import (
    ChannelBindingService,
    ChannelBindingServiceError,
    get_channel_binding_service,
)
from afkbot.services.channels.active_context import (
    ActiveChannelContext,
    active_channel_context_from_trusted,
)
from afkbot.services.channels.contracts import (
    ChannelDeliveryTarget,
    ChannelOutboundAttachment,
    ChannelOutboundMessage,
)
from afkbot.services.channels.delivery_runtime import ChannelDeliveryServiceError
from afkbot.services.channels.delivery_runtime import ResolvedDeliveryTarget
from afkbot.services.channels.delivery_runtime import resolve_delivery_target
from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointService,
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
)
from afkbot.services.channels.plugin_adapters import ChannelAdapterFactory
from afkbot.services.channels.service import ChannelDeliveryService
from afkbot.services.plugins import get_plugin_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings

_SUPPORTED_CHANNEL_SEND_TRANSPORTS = {"telegram", "telegram_user", "partyflow"}


class ChannelSendParams(ToolParameters):
    """Parameters for channel.send."""

    transport: str | None = Field(default=None, min_length=1, max_length=64)
    text: str = Field(default="", max_length=200000)
    parse_mode: str | None = Field(default=None, min_length=1, max_length=32)
    disable_web_page_preview: bool = False
    reply_markup: dict[str, object] | None = None
    attachments: tuple[ChannelOutboundAttachment, ...] = ()
    stream_draft: bool = False
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
        transport = None if self.transport is None else self.transport.strip().lower()
        self.transport = transport
        if (
            self.peer_id is None
            and self.chat_id is not None
            and (transport is None or transport != "smtp")
        ):
            self.peer_id = self.chat_id
        return self


class ChannelSendTool(ToolBase):
    """Send messages and media back through a configured external channel."""

    name = "channel.send"
    description = (
        "Send a message through a configured channel endpoint. Built-in transports are telegram, "
        "telegram_user, and partyflow; enabled plugin channel adapters may add more transports. "
        "Telegram supports optional Markdown/HTML parse_mode, inline/reply keyboards, media attachments, "
        "or Bot API draft streaming. PartyFlow and plugin channel support depends on the selected adapter. "
        "Targets need endpoint_id plus binding_id or peer_id/chat_id; telegram_user also needs account_id "
        "unless endpoint_id/binding_id supplies it. Sender credentials always come from the selected endpoint; "
        "credential_profile_key is accepted only when it matches that endpoint."
    )
    parameters_model = ChannelSendParams

    def __init__(
        self,
        settings: Settings,
        *,
        delivery_service: ChannelDeliveryService | None = None,
        endpoint_service: ChannelEndpointService | None = None,
        binding_service: ChannelBindingService | None = None,
        channel_adapters: dict[tuple[str, str], ChannelAdapterFactory] | None = None,
    ) -> None:
        self._settings = settings
        self._delivery_service = delivery_service or ChannelDeliveryService(settings)
        self._endpoint_service = endpoint_service or get_channel_endpoint_service(settings)
        self._binding_service = binding_service or get_channel_binding_service(settings)
        self._channel_adapters = channel_adapters

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, ChannelSendParams)
            else ChannelSendParams.model_validate(params.model_dump())
        )
        active_context = active_channel_context_from_trusted(ctx.trusted_runtime_context)
        active_defaults_error = self._apply_active_channel_defaults(
            payload=payload,
            active_context=active_context,
        )
        if active_defaults_error is not None:
            return active_defaults_error
        scope_error = self._ensure_profile_scope(ctx=ctx, payload=payload)
        if scope_error is not None:
            return scope_error
        if payload.transport is None:
            return ToolResult.error(
                error_code="channel_send_transport_required",
                reason=(
                    "channel.send requires transport outside an active channel turn. "
                    "Pass a built-in or plugin-registered channel transport."
                ),
            )
        if not self._is_channel_send_transport_supported(payload.transport):
            return ToolResult.error(
                error_code="channel_send_transport_not_supported",
                reason=(
                    "channel.send supports built-in transports and plugin channel adapters "
                    "that register outbound delivery."
                ),
                metadata={"transport": payload.transport},
            )
        try:
            message = ChannelOutboundMessage(
                text=payload.text,
                parse_mode=payload.parse_mode,
                disable_web_page_preview=payload.disable_web_page_preview,
                reply_markup=payload.reply_markup,
                attachments=payload.attachments,
                stream_draft=payload.stream_draft,
            )
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
            binding_error = await self._validate_binding_matches_endpoint(
                endpoint=endpoint_or_error,
                ctx=ctx,
                binding_id=payload.binding_id,
            )
            if binding_error is not None:
                return binding_error
            target = target.model_copy(update={"adapter_kind": endpoint_or_error.adapter_kind})
            resolved_target_or_error = await self._resolve_target_for_policy(target=target)
            if isinstance(resolved_target_or_error, ToolResult):
                return resolved_target_or_error
            outbound_policy_error = self._validate_outbound_policy(
                endpoint=endpoint_or_error,
                target=resolved_target_or_error,
            )
            if outbound_policy_error is not None:
                return outbound_policy_error
            target = ChannelDeliveryTarget(
                transport=resolved_target_or_error.transport,
                adapter_kind=endpoint_or_error.adapter_kind,
                binding_id=resolved_target_or_error.binding_id,
                account_id=resolved_target_or_error.account_id or endpoint_or_error.account_id,
                peer_id=resolved_target_or_error.peer_id,
                thread_id=resolved_target_or_error.thread_id,
                user_id=resolved_target_or_error.user_id,
                address=resolved_target_or_error.address,
                subject=resolved_target_or_error.subject,
            )
            credential_override_error = self._validate_credential_profile_override(
                endpoint=endpoint_or_error,
                credential_profile_key=payload.credential_profile_key,
            )
            if credential_override_error is not None:
                return credential_override_error
            credential_profile_key = endpoint_or_error.credential_profile_key
            if _is_plain_text_message(message):
                result = await self._delivery_service.deliver_text(
                    profile_id=ctx.profile_id,
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    target=target,
                    text=message.text or "",
                    credential_profile_key=credential_profile_key,
                )
            else:
                result = await self._delivery_service.deliver_message(
                    profile_id=ctx.profile_id,
                    session_id=ctx.session_id,
                    run_id=ctx.run_id,
                    target=target,
                    message=message,
                    credential_profile_key=credential_profile_key,
                )
        except ChannelDeliveryServiceError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata=exc.metadata,
            )
        except ValueError as exc:
            return ToolResult.error(
                error_code="channel_send_payload_invalid",
                reason=str(exc),
            )
        return ToolResult(ok=True, payload=_delivery_result_payload(result))

    def _is_channel_send_transport_supported(self, transport: str) -> bool:
        if transport in _SUPPORTED_CHANNEL_SEND_TRANSPORTS:
            return True
        adapters = self._channel_adapters
        if adapters is None:
            adapters = dict(get_plugin_service(self._settings).channel_adapters())
        return any(
            key[0] == transport and adapter.send_message is not None
            for key, adapter in adapters.items()
        )

    @staticmethod
    def _apply_active_channel_defaults(
        *,
        payload: ChannelSendParams,
        active_context: ActiveChannelContext | None,
    ) -> ToolResult | None:
        if active_context is None:
            return None
        if payload.endpoint_id is not None and payload.endpoint_id != active_context.endpoint_id:
            return ToolResult.error(
                error_code="channel_send_endpoint_not_active",
                reason="channel.send may only use the active inbound channel endpoint in this turn.",
                metadata={
                    "requested_endpoint_id": payload.endpoint_id,
                    "active_endpoint_id": active_context.endpoint_id,
                },
            )
        payload.transport = payload.transport or active_context.transport
        payload.endpoint_id = payload.endpoint_id or active_context.endpoint_id
        payload.account_id = payload.account_id or active_context.account_id
        payload.peer_id = payload.peer_id or active_context.peer_id
        payload.thread_id = payload.thread_id or active_context.thread_id
        payload.user_id = payload.user_id or active_context.user_id
        return None

    def _validate_outbound_policy(
        self,
        *,
        endpoint: ChannelEndpointConfig,
        target: ResolvedDeliveryTarget,
    ) -> ToolResult | None:
        allow_to = endpoint.access_policy.outbound_allow_to
        if not allow_to:
            return None
        if "*" in allow_to:
            return None
        target_key = self._outbound_policy_target_key(endpoint=endpoint, target=target)
        if target_key and target_key in allow_to:
            return None
        return ToolResult.error(
            error_code="channel_send_target_not_allowed",
            reason=(
                "channel.send target is not allowed by the endpoint outbound allowlist. "
                "Use an allowed target id or update the channel access policy."
            ),
            metadata={
                "endpoint_id": endpoint.endpoint_id,
                "transport": target.transport,
                "target_key": target_key or "",
            },
        )

    def _outbound_policy_target_key(
        self,
        *,
        endpoint: ChannelEndpointConfig,
        target: ResolvedDeliveryTarget,
    ) -> str | None:
        if endpoint.transport in _SUPPORTED_CHANNEL_SEND_TRANSPORTS:
            return (target.peer_id or "").strip() or None
        adapter = self._channel_adapter_for_endpoint(endpoint)
        if adapter is not None and adapter.outbound_target_key is not None:
            value = adapter.outbound_target_key(target)
            return (value or "").strip() or None
        return (target.peer_id or target.address or target.user_id or "").strip() or None

    def _channel_adapter_for_endpoint(
        self,
        endpoint: ChannelEndpointConfig,
    ) -> ChannelAdapterFactory | None:
        adapters = self._channel_adapters
        if adapters is None:
            adapters = dict(get_plugin_service(self._settings).channel_adapters())
        return adapters.get((endpoint.transport, endpoint.adapter_kind))

    async def _resolve_target_for_policy(
        self,
        *,
        target: ChannelDeliveryTarget,
    ) -> ResolvedDeliveryTarget | ToolResult:
        try:
            return await resolve_delivery_target(
                settings=self._settings,
                target=target,
                binding_service=self._binding_service,
                channel_adapters=self._channel_adapters,
            )
        except ChannelDeliveryServiceError as exc:
            return ToolResult.error(
                error_code=exc.error_code,
                reason=exc.reason,
                metadata=exc.metadata,
            )

    async def _validate_binding_matches_endpoint(
        self,
        *,
        endpoint: ChannelEndpointConfig,
        ctx: ToolContext,
        binding_id: str | None,
    ) -> ToolResult | None:
        if binding_id is None:
            return None
        try:
            binding = await self._binding_service.get(binding_id=binding_id)
        except ChannelBindingServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        if binding.profile_id != ctx.profile_id or binding.profile_id != endpoint.profile_id:
            return ToolResult.error(
                error_code="channel_send_binding_not_in_profile",
                reason="channel.send binding does not belong to the active profile.",
                metadata={
                    "binding_id": binding.binding_id,
                    "binding_profile_id": binding.profile_id,
                    "profile_id": ctx.profile_id,
                },
            )
        if binding.transport != endpoint.transport:
            return ToolResult.error(
                error_code="channel_send_binding_transport_mismatch",
                reason="channel.send binding transport does not match the selected endpoint.",
                metadata={
                    "binding_id": binding.binding_id,
                    "binding_transport": binding.transport,
                    "endpoint_transport": endpoint.transport,
                },
            )
        if not _binding_matches_endpoint_id(binding=binding, endpoint=endpoint):
            return ToolResult.error(
                error_code="channel_send_binding_endpoint_mismatch",
                reason="channel.send binding does not belong to the selected endpoint.",
                metadata={
                    "binding_id": binding.binding_id,
                    "endpoint_id": endpoint.endpoint_id,
                },
            )
        if binding.account_id is not None and binding.account_id != endpoint.account_id:
            return ToolResult.error(
                error_code="channel_send_binding_account_mismatch",
                reason="channel.send binding account_id does not match the selected endpoint.",
                metadata={
                    "binding_id": binding.binding_id,
                    "binding_account_id": binding.account_id,
                    "endpoint_account_id": endpoint.account_id,
                },
            )
        return None

    @staticmethod
    def _validate_credential_profile_override(
        *,
        endpoint: ChannelEndpointConfig,
        credential_profile_key: str | None,
    ) -> ToolResult | None:
        if credential_profile_key is None:
            return None
        if credential_profile_key == endpoint.credential_profile_key:
            return None
        return ToolResult.error(
            error_code="channel_send_credential_profile_mismatch",
            reason=(
                "channel.send cannot override the sender credential profile configured "
                "on the selected channel endpoint."
            ),
            metadata={
                "endpoint_id": endpoint.endpoint_id,
                "endpoint_credential_profile_key": endpoint.credential_profile_key,
            },
        )

    async def _resolve_endpoint_for_outbound_policy(
        self,
        *,
        ctx: ToolContext,
        payload: ChannelSendParams,
        target: ChannelDeliveryTarget,
    ) -> ChannelEndpointConfig | ToolResult:
        endpoint_id = payload.endpoint_id or await self._endpoint_id_from_binding_id(
            payload.binding_id
        )
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


def _is_plain_text_message(message: ChannelOutboundMessage) -> bool:
    return (
        bool(message.text)
        and message.parse_mode is None
        and not message.disable_web_page_preview
        and message.reply_markup is None
        and not message.attachments
        and not message.stream_draft
    )


def _binding_matches_endpoint_id(
    *,
    binding: ChannelBindingRule,
    endpoint: ChannelEndpointConfig,
) -> bool:
    binding_id = binding.binding_id.strip()
    endpoint_id = endpoint.endpoint_id.strip()
    return binding_id == endpoint_id or binding_id.startswith(f"{endpoint_id}:")


def _jsonish(value: Any) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(key): _jsonish(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonish(item) for item in value]
    return str(value)
