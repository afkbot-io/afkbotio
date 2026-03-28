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
from afkbot.settings import Settings


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
) -> ResolvedDeliveryTarget:
    """Resolve optional binding metadata and validate supported transports."""

    if target.binding_id is None:
        return resolved_from_target(target)
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
        account_id=target.account_id or binding.account_id,
        peer_id=target.peer_id or binding.peer_id,
        thread_id=target.thread_id or binding.thread_id,
        user_id=target.user_id or binding.user_id,
        address=target.address,
        subject=target.subject,
    )
    return resolved_from_target(merged)


def resolved_from_target(target: ChannelDeliveryTarget) -> ResolvedDeliveryTarget:
    """Validate one explicit delivery target and normalize its payload."""

    if target.transport == "telegram" and not target.peer_id:
        raise ChannelDeliveryServiceError(
            error_code="channel_delivery_target_incomplete",
            reason="Telegram delivery target requires peer_id.",
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
    if target.transport not in {"telegram", "telegram_user", "smtp"}:
        raise ChannelDeliveryServiceError(
            error_code="channel_delivery_transport_not_supported",
            reason=f"Unsupported delivery transport: {target.transport}",
            metadata={"transport": target.transport},
        )
    return ResolvedDeliveryTarget(
        transport=target.transport,
        binding_id=target.binding_id,
        account_id=target.account_id,
        peer_id=target.peer_id,
        thread_id=target.thread_id,
        user_id=target.user_id,
        address=target.address,
        subject=target.subject,
    )


def build_app_runtime_context(
    *,
    settings: Settings,
    profile_id: str,
    session_id: str,
    run_id: int,
    credential_profile_key: str | None,
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
    )
