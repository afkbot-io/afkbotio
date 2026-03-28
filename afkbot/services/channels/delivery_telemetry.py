"""In-memory outbound channel delivery telemetry."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from afkbot.services.channels.contracts import (
    ChannelDeliveryDiagnostics,
    ChannelDeliveryTelemetryEvent,
    ChannelDeliveryTransportDiagnostics,
)
from afkbot.settings import Settings

_DELIVERY_TELEMETRY_BY_ROOT: dict[str, "ChannelDeliveryTelemetry"] = {}
_DELIVERY_TELEMETRY_HISTORY_SIZE = 100


@dataclass(slots=True)
class _ChannelDeliveryTransportCounter:
    total: int = 0
    succeeded: int = 0
    failed: int = 0


class ChannelDeliveryTelemetry:
    """Per-root delivery counters and recent events."""

    def __init__(self) -> None:
        self.total = 0
        self.succeeded = 0
        self.failed = 0
        self.transports: dict[str, _ChannelDeliveryTransportCounter] = {}
        self.events: deque[ChannelDeliveryTelemetryEvent] = deque(
            maxlen=_DELIVERY_TELEMETRY_HISTORY_SIZE
        )

    def record(
        self,
        *,
        transport: str,
        ok: bool,
        error_code: str | None,
        target: dict[str, str],
    ) -> None:
        self.total += 1
        counter = self.transports.setdefault(transport, _ChannelDeliveryTransportCounter())
        counter.total += 1
        if ok:
            self.succeeded += 1
            counter.succeeded += 1
        else:
            self.failed += 1
            counter.failed += 1
        self.events.append(
            ChannelDeliveryTelemetryEvent(
                transport=transport,
                ok=ok,
                error_code=error_code,
                binding_id=target.get("binding_id"),
                account_id=target.get("account_id"),
                peer_id=target.get("peer_id"),
                thread_id=target.get("thread_id"),
                user_id=target.get("user_id"),
                address=target.get("address"),
                subject=target.get("subject"),
            )
        )

    def snapshot(self) -> ChannelDeliveryDiagnostics:
        return ChannelDeliveryDiagnostics(
            total=self.total,
            succeeded=self.succeeded,
            failed=self.failed,
            transports=tuple(
                ChannelDeliveryTransportDiagnostics(
                    transport=transport,
                    total=counter.total,
                    succeeded=counter.succeeded,
                    failed=counter.failed,
                )
                for transport, counter in sorted(self.transports.items())
            ),
            recent_events=tuple(self.events),
        )


def get_channel_delivery_telemetry(settings: Settings) -> ChannelDeliveryTelemetry:
    """Get or create per-root telemetry collector."""

    return _DELIVERY_TELEMETRY_BY_ROOT.setdefault(
        str(settings.root_dir.resolve()),
        ChannelDeliveryTelemetry(),
    )


def get_channel_delivery_diagnostics(settings: Settings) -> ChannelDeliveryDiagnostics:
    """Return current in-memory delivery telemetry for one runtime root."""

    telemetry = _DELIVERY_TELEMETRY_BY_ROOT.get(str(settings.root_dir.resolve()))
    if telemetry is None:
        return ChannelDeliveryDiagnostics(
            total=0,
            succeeded=0,
            failed=0,
            transports=(),
            recent_events=(),
        )
    return telemetry.snapshot()


def reset_channel_delivery_diagnostics() -> None:
    """Reset cached delivery telemetry for tests."""

    _DELIVERY_TELEMETRY_BY_ROOT.clear()
