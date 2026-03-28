"""Canonical channel transport contracts shared by adapters and runtime layers."""

from afkbot.services.channels.contracts import (
    ChannelDeliveryDiagnostics,
    ChannelDeliveryTarget,
    build_delivery_target_runtime_metadata,
)

__all__ = [
    "ChannelDeliveryDiagnostics",
    "ChannelDeliveryTarget",
    "build_delivery_target_runtime_metadata",
]
