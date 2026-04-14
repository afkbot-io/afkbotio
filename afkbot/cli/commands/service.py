"""Managed background-service commands for AFKBOT."""

from __future__ import annotations

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.start import run_start_command
from afkbot.services.managed_runtime_service import (
    ensure_managed_runtime_service,
    inspect_managed_runtime_service,
    restart_managed_runtime_service,
    start_managed_runtime_service,
    stop_managed_runtime_service,
)
from afkbot.services.runtime_ports import is_runtime_port_pair_available, probe_runtime_stack
from afkbot.services.setup.runtime_store import read_runtime_config, write_runtime_config
from afkbot.services.setup.state import setup_is_complete
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register managed service commands."""

    service_app = typer.Typer(
        help="Install, inspect, and control the local AFKBOT background service.",
        no_args_is_help=True,
    )

    @service_app.command("install")
    def install(
        start: bool = typer.Option(
            True,
            "--start/--no-start",
            help="Enable the managed service and start it immediately when possible.",
        ),
    ) -> None:
        """Install or refresh the local managed AFKBOT daemon service."""

        settings = get_settings()
        result = ensure_managed_runtime_service(
            settings,
            start=start and setup_is_complete(settings),
        )
        typer.echo(_format_service_result(result))
        _exit_for_service_result(
            result,
            require_running=start and setup_is_complete(settings),
        )

    @service_app.command("start")
    def start() -> None:
        """Start the managed AFKBOT daemon for this host."""

        settings = get_settings()
        result = start_managed_runtime_service(settings)
        typer.echo(_format_service_result(result))
        _exit_for_service_result(result, require_running=True)

    @service_app.command("run-managed", hidden=True)
    def run_managed() -> None:
        """Internal managed-service entrypoint used by system service definitions."""

        settings = get_settings()
        if not setup_is_complete(settings):
            raise_usage_error("Run `afk setup` before starting the managed AFKBOT daemon.")
        run_start_command(
            settings=settings,
            channels=True,
            channel_ids=(),
            strict_channels=False,
            allow_pending_upgrades=False,
        )

    @service_app.command("stop")
    def stop() -> None:
        """Stop the managed AFKBOT daemon for this host."""

        settings = get_settings()
        result = stop_managed_runtime_service(settings)
        typer.echo(_format_service_result(result))
        _exit_for_service_result(result, require_running=False)

    @service_app.command("restart")
    def restart() -> None:
        """Restart the local managed AFKBOT daemon service."""

        settings = get_settings()
        result = restart_managed_runtime_service(settings)
        typer.echo(_format_service_result(result))
        _exit_for_service_result(result, require_running=True)

    @service_app.command("status")
    def status() -> None:
        """Report whether the managed AFKBOT daemon is installed and healthy."""

        settings = get_settings()
        inspection = inspect_managed_runtime_service(settings)
        typer.echo(_format_service_inspection(inspection))
        if not _service_inspection_is_healthy(inspection):
            raise typer.Exit(code=1)

    @service_app.command("port")
    def port(
        runtime_port: int = typer.Option(
            ...,
            "--runtime-port",
            min=1,
            max=65535,
            help="Persisted runtime port used by `afk start` and the managed service.",
        ),
        host: str | None = typer.Option(
            None,
            "--host",
            help="Optional persisted runtime host override.",
        ),
    ) -> None:
        """Persist a new runtime bind and reload the managed service."""

        settings = get_settings()
        runtime_config = dict(read_runtime_config(settings))
        previous_runtime_config = dict(runtime_config)
        target_host = str(host or runtime_config.get("runtime_host") or settings.runtime_host).strip() or settings.runtime_host
        current_host = str(runtime_config.get("runtime_host") or settings.runtime_host).strip() or settings.runtime_host
        current_port = _resolve_current_runtime_port(
            runtime_config=runtime_config,
            fallback_port=settings.runtime_port,
        )
        _validate_requested_runtime_bind(
            host=target_host,
            runtime_port=runtime_port,
            current_host=current_host,
            current_runtime_port=current_port,
        )
        if host is not None:
            runtime_config["runtime_host"] = host
        runtime_config["runtime_port"] = runtime_port
        write_runtime_config(settings, config=runtime_config)
        get_settings.cache_clear()
        refreshed_settings = get_settings()
        result = ensure_managed_runtime_service(
            refreshed_settings,
            start=setup_is_complete(refreshed_settings),
        )
        port_message = _format_port_result(runtime_port=runtime_port, host=host or refreshed_settings.runtime_host)
        if str(getattr(result, "status", "")) == "failed":
            write_runtime_config(refreshed_settings, config=previous_runtime_config)
            get_settings.cache_clear()
            restored_settings = get_settings()
            restore_result = ensure_managed_runtime_service(
                restored_settings,
                start=setup_is_complete(restored_settings),
            )
            restored_host = (
                str(previous_runtime_config.get("runtime_host") or restored_settings.runtime_host).strip()
                or restored_settings.runtime_host
            )
            restored_port = _resolve_current_runtime_port(
                runtime_config=previous_runtime_config,
                fallback_port=restored_settings.runtime_port,
            )
            port_message = _format_port_restore_result(runtime_port=restored_port, host=restored_host)
            typer.echo("runtime bind rollback: restored previous runtime host/port after failed reload")
            typer.echo(_format_service_result(restore_result))
        typer.echo(port_message)
        typer.echo(_format_service_result(result))
        _exit_for_service_result(result, require_running=setup_is_complete(refreshed_settings))

    app.add_typer(service_app, name="service")


def _format_service_result(result: object) -> str:
    status = str(getattr(result, "status", "unknown"))
    kind = getattr(result, "kind", None)
    path = getattr(result, "path", None)
    reason = getattr(result, "reason", None)
    parts = [f"service: status={status}"]
    if kind is not None:
        parts.append(f"kind={kind}")
    if path is not None:
        parts.append(f"path={path}")
    if reason:
        parts.append(f"reason={reason}")
    return ", ".join(parts)


def _format_port_result(*, runtime_port: int, host: str) -> str:
    return f"runtime bind saved: host={host}, runtime_port={runtime_port}, api_port={runtime_port + 1}"


def _format_port_restore_result(*, runtime_port: int, host: str) -> str:
    return (
        f"runtime bind restored: host={host}, runtime_port={runtime_port}, api_port={runtime_port + 1}"
    )


def _format_service_inspection(inspection: object) -> str:
    installed = bool(getattr(inspection, "installed", False))
    running = bool(getattr(inspection, "running", False))
    kind = getattr(inspection, "kind", None)
    path = getattr(inspection, "path", None)
    manager_state = getattr(inspection, "manager_state", None)
    runtime_ok = bool(getattr(inspection, "runtime_ok", False))
    api_ok = bool(getattr(inspection, "api_ok", False))
    parts = [f"service: {'installed' if installed else 'not_installed'}"]
    if kind is not None:
        parts.append(f"kind={kind}")
    if path is not None:
        parts.append(f"path={path}")
    if manager_state is not None:
        parts.append(f"manager_state={manager_state}")
    parts.append(f"daemon={'running' if running else 'not_running'}")
    parts.append(f"runtime_health={'ok' if runtime_ok else 'down'}")
    parts.append(f"api_health={'ok' if api_ok else 'down'}")
    return ", ".join(parts)


def _exit_for_service_result(result: object, *, require_running: bool) -> None:
    status = str(getattr(result, "status", "unknown"))
    if status == "failed":
        raise typer.Exit(code=1)
    if require_running and status not in {"installed", "restarted"}:
        raise typer.Exit(code=1)


def _service_inspection_is_healthy(inspection: object) -> bool:
    installed = bool(getattr(inspection, "installed", False))
    runtime_ok = bool(getattr(inspection, "runtime_ok", False))
    api_ok = bool(getattr(inspection, "api_ok", False))
    kind = str(getattr(inspection, "kind", "") or "")
    manager_state = str(getattr(inspection, "manager_state", "") or "").strip().lower()
    if not installed or not runtime_ok or not api_ok:
        return False
    if kind in {"systemd-user", "systemd-system"}:
        active_state = manager_state.split(",", maxsplit=1)[0].strip()
        return active_state == "active=active"
    if kind == "launchd":
        return manager_state in {"running", "loaded", "waiting"}
    return False


def _validate_requested_runtime_bind(
    *,
    host: str,
    runtime_port: int,
    current_host: str,
    current_runtime_port: int,
) -> None:
    if is_runtime_port_pair_available(host=host, runtime_port=runtime_port):
        return
    stack_probe = probe_runtime_stack(host=host, runtime_port=runtime_port)
    if host == current_host and runtime_port == current_runtime_port and stack_probe.running:
        return
    raise_usage_error(
        "Requested runtime bind is busy. "
        f"Host={host}, runtime_port={runtime_port}, api_port={runtime_port + 1}. "
        "Choose a different port or stop the conflicting listener."
    )


def _resolve_current_runtime_port(
    *,
    runtime_config: dict[str, object],
    fallback_port: int,
) -> int:
    raw_value = runtime_config.get("runtime_port")
    if raw_value in {None, ""} or isinstance(raw_value, bool):
        return fallback_port
    if isinstance(raw_value, int):
        return raw_value
    if isinstance(raw_value, str):
        normalized = raw_value.strip()
        if not normalized:
            return fallback_port
        try:
            return int(normalized)
        except ValueError:
            return fallback_port
    return fallback_port
