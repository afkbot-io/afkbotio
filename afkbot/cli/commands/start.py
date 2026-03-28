"""Unified runtime starter for API and automations daemon."""

from __future__ import annotations

import asyncio
import signal
from contextlib import suppress
from typing import Final

import typer
import uvicorn

from afkbot.cli.command_errors import raise_usage_error
from afkbot.api.app import create_app
from afkbot.services.automations.runtime_daemon import RuntimeDaemon
from afkbot.services.channels.runtime_manager import (
    ChannelRuntimeManager,
    ChannelRuntimeManagerError,
    ChannelRuntimeStartReport,
)
from afkbot.services.upgrade import UpgradeApplyReport, UpgradeService
from afkbot.settings import Settings, get_settings

_DEFAULT_API_PORT_OFFSET: Final[int] = 1


class _ManagedUvicornServer(uvicorn.Server):
    """Uvicorn server without internal signal handler registration."""

    def install_signal_handlers(self) -> None:  # pragma: no cover - trivial override
        return


def register(app: typer.Typer) -> None:
    """Register unified runtime start command."""

    @app.command("start")
    def start(
        host: str | None = typer.Option(
            None,
            "--host",
            help="Bind host shared by the automation runtime and chat API server.",
        ),
        runtime_port: int | None = typer.Option(
            None,
            "--runtime-port",
            min=1,
            max=65535,
            help="Automation runtime port (cron + webhook ingress)",
        ),
        api_port: int | None = typer.Option(
            None,
            "--api-port",
            min=1,
            max=65535,
            help="Chat API/WS port",
        ),
        channels: bool = typer.Option(
            True,
            "--channels/--no-channels",
            help="Start enabled external channel adapters.",
        ),
        channel: list[str] = typer.Option(
            [],
            "--channel",
            help="Repeatable channel endpoint id filter. When omitted, start all enabled channels.",
        ),
        strict_channels: bool = typer.Option(
            False,
            "--strict-channels/--best-effort-channels",
            help="Abort startup on channel adapter failure instead of continuing with warnings.",
        ),
        allow_pending_upgrades: bool = typer.Option(
            False,
            "--allow-pending-upgrades",
            help="Start even when persisted-state upgrades are still pending.",
        ),
    ) -> None:
        """Start the full AFKBOT stack and stop all owned services on exit."""

        settings = get_settings()
        resolved_host = host or settings.runtime_host
        resolved_runtime_port = runtime_port or settings.runtime_port
        resolved_api_port = api_port or (resolved_runtime_port + _DEFAULT_API_PORT_OFFSET)
        if resolved_runtime_port == resolved_api_port:
            raise_usage_error("--runtime-port and --api-port must be different ports")
        if not allow_pending_upgrades:
            upgrade_report = asyncio.run(_inspect_pending_upgrades(settings))
            if upgrade_report.changed:
                details = "; ".join(
                    f"{step.name}: {step.details}"
                    for step in upgrade_report.steps
                    if step.changed
                )
                reason = "Pending persisted-state upgrades detected."
                if details:
                    reason = f"{reason} {details}"
                raise_usage_error(
                    f"{reason} Run `afk upgrade apply` first, or rerun with --allow-pending-upgrades."
                )

        typer.echo(
            f"runtime daemon: http://{resolved_host}:{resolved_runtime_port} "
            "(webhook + cron scheduler)"
        )
        typer.echo(f"chat api/ws: http://{resolved_host}:{resolved_api_port}")
        typer.echo("Press Ctrl+C to stop.")

        try:
            asyncio.run(
                _run_full_stack(
                    host=resolved_host,
                    runtime_port=resolved_runtime_port,
                    api_port=resolved_api_port,
                    start_channels=channels,
                    channel_ids=tuple(channel),
                    strict_channels=strict_channels,
                    settings=settings,
                )
            )
        except ChannelRuntimeManagerError as exc:
            raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
        except OSError as exc:
            raise_usage_error(
                _format_runtime_start_os_error(
                    exc,
                    host=resolved_host,
                    runtime_port=resolved_runtime_port,
                    api_port=resolved_api_port,
                )
            )
        except KeyboardInterrupt:
            typer.echo("\nShutting down...")


async def _run_full_stack(
    *,
    host: str,
    runtime_port: int,
    api_port: int,
    start_channels: bool,
    channel_ids: tuple[str, ...],
    strict_channels: bool,
    settings: Settings,
) -> None:
    """Run runtime daemon and API server concurrently with shared shutdown."""

    daemon = RuntimeDaemon(host=host, port=runtime_port)
    app = create_app()
    channel_manager = ChannelRuntimeManager(settings)
    server = _ManagedUvicornServer(
        uvicorn.Config(
            app=app,
            host=host,
            port=api_port,
            log_level="info",
        )
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    registered_signals: list[signal.Signals] = []
    api_task: asyncio.Task[object] | None = None
    stop_wait_task: asyncio.Task[object] | None = None

    def _request_shutdown() -> None:
        daemon.begin_shutdown()
        server.should_exit = True
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            continue
        registered_signals.append(sig)

    try:
        await daemon.start()
        if start_channels:
            channel_report = await _start_channels(
                channel_manager=channel_manager,
                endpoint_ids=channel_ids,
                strict_channels=strict_channels,
            )
            _render_channel_start_report(channel_report)
        api_task = asyncio.create_task(server.serve(), name="afk-api-server")
        stop_wait_task = asyncio.create_task(stop_event.wait(), name="afk-shutdown-wait")
        done, _ = await asyncio.wait(
            {api_task, stop_wait_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if api_task in done:
            error = api_task.exception()
            if error is not None:
                raise error
            return
        server.should_exit = True
        await api_task
    finally:
        for sig in registered_signals:
            with suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
        await channel_manager.stop()
        if stop_wait_task is not None and not stop_wait_task.done():
            stop_wait_task.cancel()
            with suppress(asyncio.CancelledError):
                await stop_wait_task
        await daemon.stop()


async def _start_channels(
    *,
    channel_manager: ChannelRuntimeManager,
    endpoint_ids: tuple[str, ...],
    strict_channels: bool,
) -> ChannelRuntimeStartReport:
    """Start channel adapters either fail-fast or in best-effort mode."""

    if strict_channels:
        started = await channel_manager.start(endpoint_ids=endpoint_ids)
        return ChannelRuntimeStartReport(started_endpoint_ids=started)
    return await channel_manager.start_best_effort(endpoint_ids=endpoint_ids)


def _render_channel_start_report(report: ChannelRuntimeStartReport) -> None:
    """Render deterministic startup warnings for degraded channel boot."""

    for failure in report.failures:
        typer.secho(
            f"WARNING [{failure.error_code}] {failure.reason}",
            fg=typer.colors.YELLOW,
            err=True,
        )


async def _inspect_pending_upgrades(settings: Settings) -> UpgradeApplyReport:
    """Inspect pending persisted-state upgrades before long-running startup."""

    service = UpgradeService(settings)
    try:
        return await service.inspect()
    finally:
        await service.shutdown()


def _format_runtime_start_os_error(
    exc: OSError,
    *,
    host: str,
    runtime_port: int,
    api_port: int,
) -> str:
    """Translate low-level bind failures into one operator-facing CLI error."""

    message = str(exc)
    if exc.errno in {48, 98} or "address already in use" in message.lower():
        return (
            "Runtime failed to bind a local port. "
            f"Host={host}, runtime_port={runtime_port}, api_port={api_port}. "
            "Choose free ports or stop the conflicting listener."
        )
    return f"Runtime startup failed: {message}"
