"""Health-check entry points used by CLI and API transports."""

from __future__ import annotations

from afkbot.db.bootstrap import create_schema, ping
from afkbot.db.engine import create_engine
from afkbot.services.health.channel_diagnostics import (
    run_channel_delivery_diagnostics,
    run_channel_health_diagnostics,
    run_channel_routing_diagnostics,
)
from afkbot.services.health.contracts import DoctorReport
from afkbot.services.health.integration_matrix import run_integration_matrix
from afkbot.services.health.runtime_support import get_missing_bootstrap
from afkbot.settings import Settings

__all__ = [
    "run_channel_delivery_diagnostics",
    "run_channel_health_diagnostics",
    "run_channel_routing_diagnostics",
    "run_doctor",
    "run_integration_matrix",
]


async def run_doctor(settings: Settings) -> DoctorReport:
    """Run bootstrap and DB checks, creating the clean SQLite schema when needed."""

    bootstrap_ok = not get_missing_bootstrap(settings)
    engine = create_engine(settings)
    try:
        await create_schema(engine)
        db_ok = await ping(engine)
    except Exception:
        db_ok = False
    finally:
        await engine.dispose()
    return DoctorReport(bootstrap_ok=bootstrap_ok, db_ok=db_ok)
