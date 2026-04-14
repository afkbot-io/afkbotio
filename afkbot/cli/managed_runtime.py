"""Best-effort reload hook for local managed runtime mutations."""

from __future__ import annotations

import typer

from afkbot.services.managed_runtime_service import (
    ManagedRuntimeServiceResult as ManagedRuntimeReloadResult,
    ensure_managed_runtime_service,
)
from afkbot.services.setup.state import setup_is_complete
from afkbot.settings import Settings, get_settings


def reload_install_managed_runtime(settings: Settings | None = None) -> ManagedRuntimeReloadResult:
    """Restart the managed host runtime when config changes require a reload."""

    resolved_settings = settings or get_settings()
    return ensure_managed_runtime_service(
        resolved_settings,
        start=setup_is_complete(resolved_settings),
    )


def reload_install_managed_runtime_notice(settings: Settings | None = None) -> None:
    """Emit one warning only when the managed runtime reload failed."""

    try:
        result = reload_install_managed_runtime(settings)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(
            "Changes were saved, but AFKBOT could not reload the managed runtime automatically "
            f"({exc.__class__.__name__}: {exc}). Restart the app manually if needed.",
            err=True,
        )
        return
    if result.status not in {"failed", "manual_restart_required"} or not result.reason:
        return
    typer.echo(result.reason, err=True)
