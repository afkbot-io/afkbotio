"""Doctor command checks bootstrap files and DB connectivity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.presentation.prompt_i18n import detect_system_prompt_language
from afkbot.services.managed_runtime_service import describe_managed_runtime_service
from afkbot.services.health import (
    DoctorChannelsReport,
    DoctorDeliveryReport,
    DoctorRoutingReport,
    HealthServiceError,
    IntegrationCheck,
    TelethonUserEndpointReport,
    TelegramPollingEndpointReport,
    get_missing_bootstrap as get_missing_bootstrap_service,
)
from afkbot.services.health import (
    run_channel_delivery_diagnostics,
    run_channel_health_diagnostics,
    run_channel_routing_diagnostics,
    run_doctor,
    run_integration_matrix,
)
from afkbot.services.channel_routing import ChannelRoutingTransportDiagnostics
from afkbot.services.channels.contracts import ChannelDeliveryTransportDiagnostics
from afkbot.services.runtime_ports import (
    is_runtime_port_pair_available,
    probe_runtime_stack,
    resolve_default_runtime_port,
)
from afkbot.services.setup.runtime_store import read_runtime_config
from afkbot.services.upgrade import UpgradeApplyReport, UpgradeService
from afkbot.settings import Settings, get_settings


def register(app: typer.Typer) -> None:
    """Register doctor command in Typer app."""

    @app.command("doctor")
    def doctor(
        integrations: bool = typer.Option(
            True,
            "--integrations/--no-integrations",
            help=(
                "Run integration matrix checks "
                "(llm/http/web.search/web.fetch/browser.control/app.list/credentials.request/telegram/imap/smtp)."
            ),
        ),
        probe: bool = typer.Option(
            False,
            "--probe/--no-probe",
            help="Enable live network probes for integrations.",
        ),
        profile: str = typer.Option(
            "default",
            "--profile",
            help="Runtime profile used for integration readiness checks.",
        ),
        routing: bool = typer.Option(
            False,
            "--routing/--no-routing",
            help="Print channel routing cutover diagnostics for this runtime root.",
        ),
        delivery: bool = typer.Option(
            False,
            "--delivery/--no-delivery",
            help="Print outbound channel delivery diagnostics for this runtime root.",
        ),
        channels: bool = typer.Option(
            False,
            "--channels/--no-channels",
            help="Print configured external channel adapter status for this runtime root.",
        ),
        upgrades: bool = typer.Option(
            True,
            "--upgrades/--no-upgrades",
            help="Check whether persisted-state upgrades are still pending for this runtime root.",
        ),
        daemon: bool = typer.Option(
            True,
            "--daemon/--no-daemon",
            help="Check whether the managed `afk start` daemon is reachable on the saved bind ports.",
        ),
        credential_profile: str = typer.Option(
            "default",
            "--credential-profile",
            help="Credential profile used when checking integration bindings.",
        ),
    ) -> None:
        """Run local health checks and apply the clean SQLite schema when needed.

        Use ``--credential-profile`` to choose which credential bindings are checked
        for integration readiness.
        """

        try:
            result = asyncio.run(
                _run_doctor(
                    get_settings(),
                    integrations=integrations or probe,
                    probe=probe,
                    profile_id=profile,
                    routing=routing,
                    delivery=delivery,
                    channels=channels,
                    upgrades=upgrades,
                    daemon=daemon,
                    credential_profile_key=credential_profile,
                )
            )
        except HealthServiceError as exc:
            raise_usage_error(exc.reason)
        if result:
            typer.echo("doctor: ok")
        else:
            raise typer.Exit(code=1)


async def _run_doctor(
    settings: Settings,
    *,
    integrations: bool = False,
    probe: bool = False,
    profile_id: str = "default",
    routing: bool = False,
    delivery: bool = False,
    channels: bool = False,
    upgrades: bool = True,
    daemon: bool = True,
    credential_profile_key: str = "default",
) -> bool:
    """Execute doctor checks and print short report."""

    report = await run_doctor(settings)
    ok_bootstrap = report.bootstrap_ok
    if ok_bootstrap:
        typer.echo("bootstrap: ok")
    else:
        typer.echo("bootstrap: missing files")

    typer.echo("db: ok" if report.db_ok else "db: failed")
    typer.echo(_format_runtime_summary(settings))
    ok = report.ok

    if daemon:
        daemon_report = _inspect_runtime_daemon(settings)
        typer.echo(_format_runtime_daemon_report(daemon_report))
        if daemon_report.required and not daemon_report.ok:
            ok = False

    if upgrades and report.db_ok:
        upgrade_report = await _inspect_upgrades(settings)
        typer.echo(_format_upgrade_report(upgrade_report))
        ok = ok and not _has_pending_upgrade_steps(upgrade_report)

    if routing:
        routing_report = await run_channel_routing_diagnostics(settings)
        typer.echo("routing:")
        typer.echo(
            f"- fallback transports: {', '.join(routing_report.fallback_transports) or '(none)'}"
        )
        typer.echo(_format_routing_totals(routing_report))
        for transport_report in routing_report.diagnostics.transports:
            typer.echo(_format_routing_transport(transport_report))

    if delivery:
        delivery_report = await run_channel_delivery_diagnostics(settings)
        typer.echo("delivery:")
        typer.echo(_format_delivery_totals(delivery_report))
        for delivery_transport_report in delivery_report.diagnostics.transports:
            typer.echo(_format_delivery_transport(delivery_transport_report))

    if channels:
        channels_report = await run_channel_health_diagnostics(settings)
        typer.echo("channels:")
        typer.echo(_format_channels_report(channels_report))

    if not integrations:
        return ok

    if not report.db_ok:
        typer.echo("integrations: unavailable (db check failed)")
        return False

    matrix = await run_integration_matrix(
        settings,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
        probe=probe,
    )
    mode = "probe" if probe else "config"
    typer.echo(f"integrations ({mode}):")
    for check in matrix.checks:
        typer.echo(_format_integration_check(check))
    return ok and matrix.ok


async def _inspect_upgrades(settings: Settings) -> UpgradeApplyReport:
    """Run non-mutating persisted-state upgrade inspection for doctor output."""

    service = UpgradeService(settings)
    try:
        return await service.inspect()
    finally:
        await service.shutdown()


def _format_integration_check(check: IntegrationCheck) -> str:
    """Render one integration matrix row for CLI output."""

    prefix = f"- {check.integration}: {check.status}"
    if check.error_code:
        return f"{prefix} ({check.error_code}) - {check.reason}"
    return f"{prefix} - {check.reason}"


def _format_runtime_summary(settings: Settings) -> str:
    """Render one short runtime bind summary for operator-facing diagnostics."""

    runtime_config = read_runtime_config(settings)
    runtime_host = str(runtime_config.get("runtime_host", settings.runtime_host)).strip() or settings.runtime_host
    runtime_port = resolve_default_runtime_port(
        settings=settings,
        host=runtime_host,
        runtime_config=runtime_config,
    )
    prompt_language = _resolve_prompt_language(runtime_config)
    return (
        "runtime: "
        f"host={runtime_host}, "
        f"runtime_port={runtime_port}, "
        f"api_port={runtime_port + 1}, "
        f"prompt_language={prompt_language}"
    )


def _resolve_prompt_language(runtime_config: Mapping[str, object]) -> str:
    normalized = str(runtime_config.get("prompt_language") or "").strip().lower()
    if normalized in {"en", "ru"}:
        return normalized
    return detect_system_prompt_language().value


@dataclass(frozen=True, slots=True)
class _RuntimeDaemonReport:
    ok: bool
    required: bool
    service_kind: str | None
    runtime_url: str
    runtime_ok: bool
    api_url: str
    api_ok: bool
    conflict: bool


def _inspect_runtime_daemon(settings: Settings) -> _RuntimeDaemonReport:
    runtime_config = read_runtime_config(settings)
    runtime_host = str(runtime_config.get("runtime_host", settings.runtime_host)).strip() or settings.runtime_host
    runtime_port = resolve_default_runtime_port(
        settings=settings,
        host=runtime_host,
        runtime_config=runtime_config,
    )
    service_status = describe_managed_runtime_service()
    stack_probe = probe_runtime_stack(host=runtime_host, runtime_port=runtime_port)
    ports_busy = not is_runtime_port_pair_available(host=runtime_host, runtime_port=runtime_port)
    conflict = ports_busy and not stack_probe.running
    required = service_status.installed or stack_probe.running or conflict
    return _RuntimeDaemonReport(
        ok=stack_probe.running,
        required=required,
        service_kind=service_status.kind,
        runtime_url=stack_probe.runtime.url,
        runtime_ok=stack_probe.runtime.ok,
        api_url=stack_probe.api.url,
        api_ok=stack_probe.api.ok,
        conflict=conflict,
    )


def _format_runtime_daemon_report(report: _RuntimeDaemonReport) -> str:
    service_label = report.service_kind or "none"
    state = "running" if report.ok else "not running"
    parts = [
        f"daemon: {state}",
        f"service={service_label}",
        f"runtime_health={'ok' if report.runtime_ok else 'down'}",
        f"api_health={'ok' if report.api_ok else 'down'}",
    ]
    if report.conflict:
        parts.append("configured ports are busy but AFKBOT health probes failed")
    return ", ".join(parts)


def _format_upgrade_report(report: UpgradeApplyReport) -> str:
    """Render one short persisted-state upgrade diagnostic summary."""

    if not _has_pending_upgrade_steps(report):
        return "upgrades: ok"
    pending = [step for step in report.steps if step.changed]
    details = "; ".join(f"{step.name}: {step.details}" for step in pending)
    return f"upgrades: pending - {details}"


def _has_pending_upgrade_steps(report: UpgradeApplyReport) -> bool:
    """Return whether upgrade inspection found actionable pending steps."""

    return any(step.changed for step in report.steps)


def _format_routing_totals(report: DoctorRoutingReport) -> str:
    diagnostics = report.diagnostics
    return (
        "- totals: "
        f"total={diagnostics.total}, "
        f"matched={diagnostics.matched}, "
        f"fallback_used={diagnostics.fallback_used}, "
        f"no_match={diagnostics.no_match}, "
        f"strict_no_match={diagnostics.strict_no_match}"
    )


def _format_routing_transport(transport_report: ChannelRoutingTransportDiagnostics) -> str:
    return (
        f"- {transport_report.transport}: "
        f"total={transport_report.total}, "
        f"matched={transport_report.matched}, "
        f"fallback_used={transport_report.fallback_used}, "
        f"no_match={transport_report.no_match}, "
        f"strict_no_match={transport_report.strict_no_match}"
    )


def _format_delivery_totals(report: DoctorDeliveryReport) -> str:
    diagnostics = report.diagnostics
    return (
        "- totals: "
        f"total={diagnostics.total}, "
        f"succeeded={diagnostics.succeeded}, "
        f"failed={diagnostics.failed}"
    )


def _format_delivery_transport(transport_report: ChannelDeliveryTransportDiagnostics) -> str:
    return (
        f"- {transport_report.transport}: "
        f"total={transport_report.total}, "
        f"succeeded={transport_report.succeeded}, "
        f"failed={transport_report.failed}"
    )


def _format_channels_report(report: DoctorChannelsReport) -> str:
    lines = [f"- telegram_polling: endpoints={len(report.telegram_polling)}"]
    for telegram_endpoint in report.telegram_polling:
        lines.append(_format_telegram_endpoint_report(telegram_endpoint))
    lines.append(f"- telethon_userbot: endpoints={len(report.telethon_userbot)}")
    for telethon_endpoint in report.telethon_userbot:
        lines.append(_format_telethon_endpoint_report(telethon_endpoint))
    return "\n".join(lines)


def _format_telegram_endpoint_report(endpoint: TelegramPollingEndpointReport) -> str:
    """Render one Telegram endpoint line for doctor channel diagnostics."""

    return (
        "  "
        f"- {endpoint.endpoint_id}: "
        f"enabled={endpoint.enabled}, "
        f"profile_id={endpoint.profile_id}, "
        f"profile_valid={endpoint.profile_valid}, "
        f"profile_exists={endpoint.profile_exists}, "
        f"credential_profile_key={endpoint.credential_profile_key}, "
        f"account_id={endpoint.account_id}, "
        f"token_configured={endpoint.token_configured}, "
        f"binding_count={endpoint.binding_count}, "
        f"state_present={endpoint.state_present}"
    )


def _format_telethon_endpoint_report(endpoint: TelethonUserEndpointReport) -> str:
    """Render one Telethon endpoint line for doctor channel diagnostics."""

    return (
        "  "
        f"- {endpoint.endpoint_id}: "
        f"enabled={endpoint.enabled}, "
        f"profile_id={endpoint.profile_id}, "
        f"profile_valid={endpoint.profile_valid}, "
        f"profile_exists={endpoint.profile_exists}, "
        f"credential_profile_key={endpoint.credential_profile_key}, "
        f"account_id={endpoint.account_id}, "
        f"api_id_configured={endpoint.api_id_configured}, "
        f"api_hash_configured={endpoint.api_hash_configured}, "
        f"session_string_configured={endpoint.session_string_configured}, "
        f"phone_configured={endpoint.phone_configured}, "
        f"policy_allows_runtime={endpoint.policy_allows_runtime}, "
        f"binding_count={endpoint.binding_count}, "
        f"state_present={endpoint.state_present}"
    )


def get_missing_bootstrap(settings: Settings) -> list[Path]:
    """Return missing bootstrap file paths."""

    return get_missing_bootstrap_service(settings)
