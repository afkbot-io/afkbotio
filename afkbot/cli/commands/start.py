"""Unified runtime starter for API, automations daemon, and Task Flow runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import os
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
from afkbot.services.runtime_ports import (
    find_available_runtime_port,
    is_runtime_port_pair_available,
    probe_runtime_stack,
    resolve_default_runtime_port,
)
from afkbot.services.setup.runtime_store import read_runtime_config, write_runtime_config
from afkbot.services.task_flow.runtime_daemon import TaskFlowRuntimeDaemon
from afkbot.services.upgrade import UpgradeApplyReport, UpgradeService
from afkbot.settings import Settings, get_settings

_DEFAULT_API_PORT_OFFSET: Final[int] = 1


@dataclass(frozen=True, slots=True)
class _RuntimeBindPlan:
    """Resolved runtime bind plus persistence/repair metadata."""

    runtime_port: int
    api_port: int
    persist_runtime_bind: bool
    repaired_from_port: int | None = None


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
        runtime_config = read_runtime_config(settings)
        bind_plan = _resolve_runtime_bind_plan(
            settings=settings,
            host=resolved_host,
            runtime_config=runtime_config,
            runtime_port_override=runtime_port,
            api_port_override=api_port,
        )
        resolved_runtime_port = bind_plan.runtime_port
        resolved_api_port = bind_plan.api_port
        persist_runtime_bind = bind_plan.persist_runtime_bind
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
        if bind_plan.repaired_from_port is not None:
            typer.secho(
                "WARNING configured runtime port pair was busy and AFKBOT was not reachable there; "
                f"switching from runtime_port={bind_plan.repaired_from_port} "
                f"to runtime_port={resolved_runtime_port} and saving it.",
                fg=typer.colors.YELLOW,
                err=True,
            )

        typer.echo(
            f"runtime daemon: http://{resolved_host}:{resolved_runtime_port} "
            "(webhook + cron scheduler + task flow workers)"
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
                    persist_runtime_bind=persist_runtime_bind,
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
        except RuntimeError as exc:
            raise_usage_error(f"Runtime startup failed: {exc}")
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
    persist_runtime_bind: bool,
    settings: Settings,
) -> None:
    """Run background runtimes and API server concurrently with shared shutdown."""

    automation_daemon = RuntimeDaemon(host=host, port=runtime_port)
    taskflow_daemon = TaskFlowRuntimeDaemon(settings=settings)
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
        automation_daemon.begin_shutdown()
        taskflow_daemon.begin_shutdown()
        server.should_exit = True
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            continue
        registered_signals.append(sig)

    try:
        await automation_daemon.start()
        await taskflow_daemon.start()
        if persist_runtime_bind:
            _persist_runtime_bind_defaults(
                settings=settings,
                host=host,
                runtime_port=runtime_port,
            )
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
            if stop_event.is_set():
                return
            raise RuntimeError("Chat API server exited before shutdown was requested.")
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
        await taskflow_daemon.stop()
        await automation_daemon.stop()


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


def _resolve_runtime_bind_plan(
    *,
    settings: Settings,
    host: str,
    runtime_config: Mapping[str, object],
    runtime_port_override: int | None,
    api_port_override: int | None,
) -> _RuntimeBindPlan:
    """Resolve one start-time runtime bind, repairing saved conflicts when safe."""

    runtime_port = (
        runtime_port_override
        if runtime_port_override is not None
        else resolve_default_runtime_port(
            settings=settings,
            host=host,
            runtime_config=runtime_config,
        )
    )
    api_port = api_port_override or (runtime_port + _DEFAULT_API_PORT_OFFSET)
    if runtime_port == api_port:
        raise_usage_error("--runtime-port and --api-port must be different ports")
    if runtime_port_override is not None or api_port_override is not None:
        return _RuntimeBindPlan(
            runtime_port=runtime_port,
            api_port=api_port,
            persist_runtime_bind=False,
        )

    if is_runtime_port_pair_available(host=host, runtime_port=runtime_port):
        return _RuntimeBindPlan(
            runtime_port=runtime_port,
            api_port=api_port,
            persist_runtime_bind=_should_persist_runtime_bind_defaults(
                runtime_config=runtime_config,
                runtime_port_override=runtime_port_override,
                api_port_override=api_port_override,
            ),
        )

    stack_probe = probe_runtime_stack(
        host=host,
        runtime_port=runtime_port,
        api_port=api_port,
    )
    if stack_probe.running:
        raise_usage_error(
            "AFKBOT daemon is already running on the configured local ports. "
            f"Host={host}, runtime_port={runtime_port}, api_port={api_port}. "
            "Stop it first or change the saved port."
        )
    if runtime_port_override is not None or api_port_override is not None:
        raise_usage_error(
            "Runtime failed to bind a local port. "
            f"Host={host}, runtime_port={runtime_port}, api_port={api_port}. "
            "Choose free ports or stop the conflicting listener."
        )

    repaired_port = find_available_runtime_port(host=host, preferred_port=runtime_port)
    if repaired_port != runtime_port and is_runtime_port_pair_available(
        host=host,
        runtime_port=repaired_port,
    ):
        return _RuntimeBindPlan(
            runtime_port=repaired_port,
            api_port=repaired_port + _DEFAULT_API_PORT_OFFSET,
            persist_runtime_bind=True,
            repaired_from_port=runtime_port,
        )
    raise_usage_error(
        "Runtime failed to bind a local port. "
        f"Host={host}, runtime_port={runtime_port}, api_port={api_port}. "
        "Choose free ports or stop the conflicting listener."
    )


def _should_persist_runtime_bind_defaults(
    *,
    runtime_config: Mapping[str, object],
    runtime_port_override: int | None,
    api_port_override: int | None,
) -> bool:
    """Return whether `afk start` should persist the auto-selected runtime bind."""

    if runtime_port_override is not None or api_port_override is not None:
        return False
    if str(os.getenv("AFKBOT_RUNTIME_PORT") or "").strip():
        return False
    return runtime_config.get("runtime_port") in {None, ""}


def _persist_runtime_bind_defaults(
    *,
    settings: Settings,
    host: str,
    runtime_port: int,
) -> None:
    """Persist the first successful runtime bind so future starts reuse it."""

    runtime_config = dict(read_runtime_config(settings))
    runtime_config.setdefault("runtime_host", host)
    runtime_config["runtime_port"] = runtime_port
    write_runtime_config(settings, config=runtime_config)
