"""Canonical channel transport contracts shared by adapters and runtime layers."""

from afkbot.services.channels.contracts import (
    ChannelDeliveryDiagnostics,
    ChannelDeliveryTarget,
    ChannelOutboundAttachment,
    ChannelOutboundMessage,
    build_delivery_target_runtime_metadata,
)

__all__ = [
    "ChannelDeliveryDiagnostics",
    "ChannelDeliveryTarget",
    "ChannelOutboundAttachment",
    "ChannelOutboundMessage",
    "build_delivery_target_runtime_metadata",
]
