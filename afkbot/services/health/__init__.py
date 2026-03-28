"""Health-check services."""

from afkbot.services.health.contracts import (
    DoctorChannelsReport,
    DoctorDeliveryReport,
    DoctorReport,
    DoctorRoutingReport,
    HealthServiceError,
    IntegrationCheck,
    IntegrationMatrixReport,
    TelethonUserEndpointReport,
    TelegramPollingEndpointReport,
)
from afkbot.services.health.service import (
    run_channel_health_diagnostics,
    run_channel_delivery_diagnostics,
    run_channel_routing_diagnostics,
    run_doctor,
    run_integration_matrix,
)
from afkbot.services.health.runtime_support import get_missing_bootstrap

__all__ = [
    "DoctorDeliveryReport",
    "DoctorReport",
    "DoctorRoutingReport",
    "DoctorChannelsReport",
    "HealthServiceError",
    "IntegrationCheck",
    "IntegrationMatrixReport",
    "TelethonUserEndpointReport",
    "TelegramPollingEndpointReport",
    "get_missing_bootstrap",
    "run_channel_health_diagnostics",
    "run_channel_delivery_diagnostics",
    "run_channel_routing_diagnostics",
    "run_doctor",
    "run_integration_matrix",
]
