"""Shared delivery runtime helpers for outbound channel transports."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.apps.contracts import AppRuntimeContext
from afkbot.services.channel_routing.service import (
    ChannelBindingService,
    ChannelBindingServiceError,
    get_channel_binding_service,
)
from afkbot.services.channels.contracts import ChannelDeliveryTarget
from afkbot.services.channels.plugin_adapters import ChannelAdapterFactory, channel_adapter_key
from afkbot.services.plugins import get_plugin_service
from afkbot.settings import Settings

CHANNEL_RUNTIME_APP_TOOL_GRANTS: tuple[str, ...] = ("app.run",)
PARTYFLOW_CHANNEL_API_HOSTS: tuple[str, ...] = ("api.partyflow.ru",)
TELEGRAM_CHANNEL_API_HOSTS: tuple[str, ...] = ("api.telegram.org",)


class ChannelDeliveryServiceError(ValueError):
    """Structured channel delivery failure."""

    def __init__(
        self,
        *,
        error_code: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason
        self.metadata = {} if metadata is None else metadata


@dataclass(frozen=True, slots=True)
class ResolvedDeliveryTarget:
    """Fully validated outbound target ready for transport delivery."""

    transport: str
    adapter_kind: str | None
    binding_id: str | None
    account_id: str | None
    peer_id: str | None
    thread_id: str | None
    user_id: str | None
    address: str | None
    subject: str | None

    def to_payload(self) -> dict[str, str]:
        payload = {
            "transport": self.transport,
            "adapter_kind": self.adapter_kind,
            "binding_id": self.binding_id,
            "account_id": self.account_id,
            "peer_id": self.peer_id,
            "thread_id": self.thread_id,
            "user_id": self.user_id,
            "address": self.address,
            "subject": self.subject,
        }
        return {key: value for key, value in payload.items() if value is not None}


async def resolve_delivery_target(
    *,
    settings: Settings,
    target: ChannelDeliveryTarget,
    binding_service: ChannelBindingService | None = None,
    channel_adapters: dict[tuple[str, str], ChannelAdapterFactory] | None = None,
) -> ResolvedDeliveryTarget:
    """Resolve optional binding metadata and validate supported transports."""

    if target.binding_id is None:
        return resolved_from_target(
            settings=settings,
            target=target,
            channel_adapters=channel_adapters,
        )
    service = binding_service or get_channel_binding_service(settings)
    try:
        binding = await service.get(binding_id=target.binding_id)
    except ChannelBindingServiceError as exc:
        raise ChannelDeliveryServiceError(
            error_code=exc.error_code,
            reason=exc.reason,
        ) from exc
    if not binding.enabled:
        raise ChannelDeliveryServiceError(
            error_code="channel_binding_disabled",
            reason=f"Channel binding '{binding.binding_id}' is disabled.",
            metadata={"binding_id": binding.binding_id},
        )
    if binding.transport != target.transport:
        raise ChannelDeliveryServiceError(
            error_code="channel_delivery_transport_mismatch",
            reason=(
                f"Delivery target transport '{target.transport}' "
                f"does not match binding transport '{binding.transport}'."
            ),
            metadata={
                "binding_id": binding.binding_id,
                "target_transport": target.transport,
                "binding_transport": binding.transport,
            },
        )
    merged = ChannelDeliveryTarget(
        transport=binding.transport,
        binding_id=binding.binding_id,
        adapter_kind=target.adapter_kind,
        account_id=target.account_id or binding.account_id,
        peer_id=target.peer_id or binding.peer_id,
        thread_id=target.thread_id or binding.thread_id,
        user_id=target.user_id or binding.user_id,
        address=target.address,
        subject=target.subject,
    )
    return resolved_from_target(
        settings=settings,
        target=merged,
        channel_adapters=channel_adapters,
    )


def resolved_from_target(
    target: ChannelDeliveryTarget,
    *,
    settings: Settings | None = None,
    channel_adapters: dict[tuple[str, str], ChannelAdapterFactory] | None = None,
) -> ResolvedDeliveryTarget:
    """Validate one explicit delivery target and normalize its payload."""

    if target.transport == "telegram" and not target.peer_id:
        raise ChannelDeliveryServiceError(
            error_code="channel_delivery_target_incomplete",
            reason="Telegram delivery target requires peer_id.",
            metadata=target.model_dump(exclude_none=True),
        )
    if target.transport == "partyflow" and not target.peer_id:
        raise ChannelDeliveryServiceError(
            error_code="channel_delivery_target_incomplete",
            reason="PartyFlow delivery target requires peer_id.",
            metadata=target.model_dump(exclude_none=True),
        )
    if target.transport == "telegram_user" and (not target.account_id or not target.peer_id):
        raise ChannelDeliveryServiceError(
            error_code="channel_delivery_target_incomplete",
            reason="Telegram user delivery target requires account_id and peer_id.",
            metadata=target.model_dump(exclude_none=True),
        )
    if target.transport == "smtp" and not target.address:
        raise ChannelDeliveryServiceError(
            error_code="channel_delivery_target_incomplete",
            reason="SMTP delivery target requires address.",
            metadata=target.model_dump(exclude_none=True),
        )
    if target.transport not in {"telegram", "telegram_user", "smtp", "partyflow"}:
        adapter = _resolve_plugin_channel_adapter(
            target=target,
            settings=settings,
            channel_adapters=channel_adapters,
        )
        if adapter is None:
            raise ChannelDeliveryServiceError(
                error_code="channel_delivery_transport_not_supported",
                reason=f"Unsupported delivery transport: {target.transport}",
                metadata={"transport": target.transport},
            )
        if adapter.validate_target is not None:
            target = adapter.validate_target(target)
    return ResolvedDeliveryTarget(
        transport=target.transport,
        adapter_kind=target.adapter_kind,
        binding_id=target.binding_id,
        account_id=target.account_id,
        peer_id=target.peer_id,
        thread_id=target.thread_id,
        user_id=target.user_id,
        address=target.address,
        subject=target.subject,
    )


def _resolve_plugin_channel_adapter(
    *,
    target: ChannelDeliveryTarget,
    settings: Settings | None,
    channel_adapters: dict[tuple[str, str], ChannelAdapterFactory] | None,
) -> ChannelAdapterFactory | None:
    if target.adapter_kind:
        adapters = channel_adapters
        if adapters is None and settings is not None:
            adapters = dict(get_plugin_service(settings).channel_adapters())
        if adapters is None:
            return None
        return adapters.get(
            channel_adapter_key(transport=target.transport, adapter_kind=target.adapter_kind)
        )
    adapters = channel_adapters
    if adapters is None and settings is not None:
        adapters = dict(get_plugin_service(settings).channel_adapters())
    if adapters is None:
        return None
    matches = [adapter for key, adapter in adapters.items() if key[0] == target.transport]
    if len(matches) == 1:
        return matches[0]
    return None


def build_app_runtime_context(
    *,
    settings: Settings,
    profile_id: str,
    session_id: str,
    run_id: int,
    credential_profile_key: str | None,
    approved_tool_names: tuple[str, ...] = (),
    approved_network_hosts: tuple[str, ...] = (),
) -> AppRuntimeContext:
    """Build consistent AppRuntime context for outbound delivery transports."""

    return AppRuntimeContext(
        profile_id=profile_id,
        session_id=session_id,
        run_id=run_id,
        credential_profile_key=credential_profile_key,
        timeout_sec=min(
            max(1, settings.tool_timeout_default_sec),
            settings.tool_timeout_max_sec,
        ),
        approved_tool_names=approved_tool_names,
        approved_network_hosts=approved_network_hosts,
    )
