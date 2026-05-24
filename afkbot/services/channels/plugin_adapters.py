"""Contracts for plugin-provided channel adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from afkbot.services.channels.contracts import ChannelDeliveryTarget, ChannelOutboundMessage
from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig
from afkbot.services.config_schema import (
    dump_json_config_fields,
    normalize_json_config_fields,
    validate_json_config_payload,
)

if TYPE_CHECKING:
    from afkbot.services.channels.delivery_runtime import ResolvedDeliveryTarget
    from afkbot.settings import Settings


class ChannelRuntimeService(Protocol):
    """Runtime service started by the channel manager."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


ChannelRuntimeBuilder = Callable[
    ["Settings", ChannelEndpointConfig, Path],
    ChannelRuntimeService,
]
ChannelEndpointConfigValidator = Callable[[ChannelEndpointConfig], ChannelEndpointConfig]
ChannelMessageSender = Callable[
    ["Settings", "ResolvedDeliveryTarget", ChannelOutboundMessage, str | None],
    Awaitable[dict[str, object]],
]
ChannelTargetValidator = Callable[[ChannelDeliveryTarget], ChannelDeliveryTarget]
ChannelOutboundTargetKey = Callable[["ResolvedDeliveryTarget"], str | None]
ChannelAdapterKey = tuple[str, str]

BUILTIN_CHANNEL_ADAPTER_KEYS: tuple[ChannelAdapterKey, ...] = (
    ("telegram", "telegram_bot_polling"),
    ("telegram_user", "telethon_userbot"),
    ("partyflow", "partyflow_polling"),
)
RESERVED_CHANNEL_TRANSPORTS: tuple[str, ...] = (
    "telegram",
    "telegram_user",
    "partyflow",
    "smtp",
)


@dataclass(frozen=True, slots=True)
class ChannelAdapterFactory:
    """Runtime hooks contributed by a plugin for one channel adapter."""

    transport: str
    adapter_kind: str
    build_runtime: ChannelRuntimeBuilder | None = None
    send_message: ChannelMessageSender | None = None
    validate_endpoint_config: ChannelEndpointConfigValidator | None = None
    validate_target: ChannelTargetValidator | None = None
    outbound_target_key: ChannelOutboundTargetKey | None = None
    setup_instructions: str = ""
    endpoint_config_schema: Mapping[str, object] = field(default_factory=dict)
    label: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        transport = _normalize_token(self.transport, label="transport")
        adapter_kind = _normalize_token(self.adapter_kind, label="adapter_kind")
        endpoint_config_fields = normalize_json_config_fields(self.endpoint_config_schema)
        object.__setattr__(self, "transport", transport)
        object.__setattr__(self, "adapter_kind", adapter_kind)
        object.__setattr__(
            self,
            "endpoint_config_schema",
            dump_json_config_fields(endpoint_config_fields),
        )

    def validate_config_schema_payload(
        self,
        endpoint: ChannelEndpointConfig,
    ) -> ChannelEndpointConfig:
        """Validate endpoint.config against this adapter's declared field schema."""

        if not self.endpoint_config_schema:
            return endpoint
        validated_config = validate_json_config_payload(
            schema_fields=self.endpoint_config_schema,
            payload=endpoint.config,
            config_label=f"{self.transport}/{self.adapter_kind} endpoint",
        )
        return endpoint.model_copy(update={"config": validated_config})


def channel_adapter_key(*, transport: str, adapter_kind: str) -> ChannelAdapterKey:
    """Return the normalized lookup key for one adapter."""

    return (
        _normalize_token(transport, label="transport"),
        _normalize_token(adapter_kind, label="adapter_kind"),
    )


def _normalize_token(value: str, *, label: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError(f"Channel adapter {label} is required")
    return normalized
