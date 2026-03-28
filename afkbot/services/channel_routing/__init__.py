"""Channel routing and session policy helpers."""

from afkbot.services.channel_routing.contracts import (
    ChannelBindingRule,
    ChannelRoutingDiagnostics,
    ChannelRoutingDecision,
    ChannelRoutingInput,
    ChannelRoutingTelemetryEvent,
    ChannelRoutingTransportDiagnostics,
    SessionPolicy,
)
from afkbot.services.channel_routing.policy import (
    allow_binding_fallback,
    normalize_transport_name,
    requires_strict_binding_match,
)
from afkbot.services.channel_routing.resolver import resolve_channel_binding, resolve_session_id
from afkbot.services.channel_routing.runtime_target import (
    RoutingSelectors,
    RuntimeTarget,
    build_routing_context_overrides,
    build_routing_runtime_metadata,
    build_routing_selectors,
    resolve_runtime_target,
)
from afkbot.services.channel_routing.service import (
    ChannelBindingService,
    ChannelBindingServiceError,
    get_channel_binding_service,
    reset_channel_binding_services,
    reset_channel_binding_services_async,
)

__all__ = [
    "ChannelBindingRule",
    "ChannelBindingService",
    "ChannelBindingServiceError",
    "ChannelRoutingDecision",
    "ChannelRoutingDiagnostics",
    "ChannelRoutingInput",
    "ChannelRoutingTelemetryEvent",
    "ChannelRoutingTransportDiagnostics",
    "RoutingSelectors",
    "RuntimeTarget",
    "SessionPolicy",
    "allow_binding_fallback",
    "build_routing_context_overrides",
    "build_routing_runtime_metadata",
    "build_routing_selectors",
    "get_channel_binding_service",
    "normalize_transport_name",
    "reset_channel_binding_services",
    "reset_channel_binding_services_async",
    "resolve_channel_binding",
    "resolve_runtime_target",
    "resolve_session_id",
    "requires_strict_binding_match",
]
