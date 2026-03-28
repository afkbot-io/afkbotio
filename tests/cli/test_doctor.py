"""Tests for doctor command internals."""

from __future__ import annotations

from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.commands.doctor import _run_doctor, get_missing_bootstrap
from afkbot.cli.main import app
from afkbot.services.health import (
    DoctorChannelsReport,
    DoctorDeliveryReport,
    DoctorRoutingReport,
    HealthServiceError,
    IntegrationCheck,
    IntegrationMatrixReport,
    TelegramPollingEndpointReport,
)
from afkbot.services.channel_routing import ChannelRoutingDiagnostics
from afkbot.services.channels.contracts import ChannelDeliveryDiagnostics
from afkbot.services.upgrade import UpgradeApplyReport, UpgradeStepReport
from afkbot.settings import Settings
from tests.cli._rendering import invoke_plain_help


async def test_doctor_success(tmp_path: Path) -> None:
    """Doctor should return True when bootstrap and DB are healthy."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor.db'}", root_dir=tmp_path)
    assert await _run_doctor(settings) is True


def test_get_missing_bootstrap(tmp_path: Path) -> None:
    """Missing file helper should return absent files."""

    settings = Settings(root_dir=tmp_path)
    missing = get_missing_bootstrap(settings)
    assert len(missing) == 4
    assert all(isinstance(path, Path) for path in missing)


async def test_doctor_integrations_success(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """Doctor should print integration matrix and return True when checks have no failures."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    async def _fake_matrix(*args: object, **kwargs: object) -> IntegrationMatrixReport:
        return IntegrationMatrixReport(
            checks=(
                IntegrationCheck(integration="http", status="ok", mode="config", reason="ready"),
                IntegrationCheck(
                    integration="telegram",
                    status="skip",
                    mode="config",
                    reason="Missing credentials: telegram_token",
                    error_code="credentials_missing",
                ),
            )
        )

    monkeypatch.setattr("afkbot.cli.commands.doctor.run_integration_matrix", _fake_matrix)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor_integration.db'}", root_dir=tmp_path
    )
    assert await _run_doctor(settings, integrations=True, probe=False) is True
    out = capsys.readouterr().out
    assert "integrations (config):" in out
    assert "- http: ok - ready" in out


async def test_doctor_integrations_fail(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Doctor should return False when matrix contains a failed integration."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    async def _fake_matrix(*args: object, **kwargs: object) -> IntegrationMatrixReport:
        return IntegrationMatrixReport(
            checks=(
                IntegrationCheck(
                    integration="http",
                    status="fail",
                    mode="probe",
                    reason="network error",
                    error_code="integration_probe_failed",
                ),
            )
        )

    monkeypatch.setattr("afkbot.cli.commands.doctor.run_integration_matrix", _fake_matrix)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor_integration_fail.db'}", root_dir=tmp_path
    )
    assert await _run_doctor(settings, integrations=True, probe=True) is False


async def test_doctor_reports_pending_upgrades(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """Doctor should fail cleanly when persisted-state upgrades are still pending."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    async def _fake_inspect(_settings: Settings) -> UpgradeApplyReport:
        return UpgradeApplyReport(
            changed=True,
            steps=(
                UpgradeStepReport(
                    name="setup_state",
                    changed=True,
                    details="setup marker needs canonical rewrite or legacy-marker cleanup",
                ),
            ),
        )

    monkeypatch.setattr("afkbot.cli.commands.doctor._inspect_upgrades", _fake_inspect)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor_upgrade_pending.db'}", root_dir=tmp_path
    )

    assert await _run_doctor(settings, integrations=False, upgrades=True) is False
    out = capsys.readouterr().out
    assert "upgrades: pending -" in out
    assert "setup_state" in out


async def test_doctor_ignores_noop_upgrade_report(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """Doctor should keep output and exit status aligned for noop upgrade reports."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    async def _fake_inspect(_settings: Settings) -> UpgradeApplyReport:
        return UpgradeApplyReport(
            changed=True,
            steps=(
                UpgradeStepReport(
                    name="noop",
                    changed=False,
                    details="inspection touched state but found no pending rewrite",
                ),
            ),
        )

    monkeypatch.setattr("afkbot.cli.commands.doctor._inspect_upgrades", _fake_inspect)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor_upgrade_noop.db'}", root_dir=tmp_path
    )

    assert await _run_doctor(settings, integrations=False, upgrades=True) is True
    out = capsys.readouterr().out
    assert "upgrades: ok" in out


def test_doctor_cli_enables_integrations_by_default(monkeypatch: MonkeyPatch) -> None:
    """CLI doctor should run integration matrix by default when no flags are provided."""

    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    captured: dict[str, object] = {}

    async def _fake_run_doctor(
        settings: Settings,
        *,
        integrations: bool = False,
        probe: bool = False,
        profile_id: str = "default",
        routing: bool = False,
        delivery: bool = False,
        channels: bool = False,
        upgrades: bool = True,
        credential_profile_key: str = "default",
    ) -> bool:
        _ = settings
        captured["integrations"] = integrations
        captured["probe"] = probe
        captured["profile_id"] = profile_id
        captured["routing"] = routing
        captured["delivery"] = delivery
        captured["channels"] = channels
        captured["upgrades"] = upgrades
        captured["credential_profile_key"] = credential_profile_key
        return True

    monkeypatch.setattr("afkbot.cli.commands.doctor._run_doctor", _fake_run_doctor)
    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "doctor: ok" in result.stdout
    assert captured == {
        "integrations": True,
        "probe": False,
        "profile_id": "default",
        "routing": False,
        "delivery": False,
        "channels": False,
        "upgrades": True,
        "credential_profile_key": "default",
    }


def test_doctor_help_mentions_sqlite_bootstrap(monkeypatch: MonkeyPatch) -> None:
    """Doctor help should explain that SQLite schema setup happens automatically."""

    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    result, output = invoke_plain_help(runner, app, ["doctor"])

    assert result.exit_code == 0
    assert "apply the clean SQLite schema when needed" in output
    assert "--credential-profile" in output
    assert "--upgrades" in output
    assert "llm" in output


async def test_doctor_routing_prints_diagnostics(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """Doctor should print routing cutover diagnostics when requested."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    async def _fake_routing(_settings: Settings) -> DoctorRoutingReport:
        return DoctorRoutingReport(
            diagnostics=ChannelRoutingDiagnostics(
                total=3,
                matched=1,
                fallback_used=1,
                no_match=2,
                strict_no_match=1,
                transports=(),
                recent_events=(),
            ),
            fallback_transports=("api", "automation", "cli"),
        )

    monkeypatch.setattr("afkbot.cli.commands.doctor.run_channel_routing_diagnostics", _fake_routing)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor_routing.db'}", root_dir=tmp_path
    )

    assert await _run_doctor(settings, integrations=False, routing=True) is True
    out = capsys.readouterr().out
    assert "routing:" in out
    assert "fallback transports: api, automation, cli" in out
    assert "total=3" in out


async def test_doctor_delivery_and_channels_print_diagnostics(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """Doctor should print delivery and channel adapter diagnostics when requested."""

    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    async def _fake_delivery(_settings: Settings) -> DoctorDeliveryReport:
        return DoctorDeliveryReport(
            diagnostics=ChannelDeliveryDiagnostics(
                total=5,
                succeeded=4,
                failed=1,
                transports=(),
                recent_events=(),
            )
        )

    async def _fake_channels(_settings: Settings) -> DoctorChannelsReport:
        return DoctorChannelsReport(
            telegram_polling=(
                TelegramPollingEndpointReport(
                    endpoint_id="support-bot",
                    enabled=True,
                    profile_id="default",
                    credential_profile_key="bot-main",
                    account_id="telegram-bot",
                    profile_valid=True,
                    profile_exists=True,
                    token_configured=True,
                    binding_count=2,
                    state_path="/tmp/state.json",
                    state_present=False,
                ),
            )
        )

    monkeypatch.setattr(
        "afkbot.cli.commands.doctor.run_channel_delivery_diagnostics", _fake_delivery
    )
    monkeypatch.setattr("afkbot.cli.commands.doctor.run_channel_health_diagnostics", _fake_channels)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'doctor_channels.db'}", root_dir=tmp_path
    )

    assert await _run_doctor(settings, integrations=False, delivery=True, channels=True) is True
    out = capsys.readouterr().out
    assert "delivery:" in out
    assert "succeeded=4" in out
    assert "channels:" in out
    assert "telegram_polling: endpoints=1" in out


def test_doctor_missing_profile_returns_usage_error(monkeypatch: MonkeyPatch) -> None:
    """CLI doctor should surface missing profile as a usage error."""

    runner = CliRunner()
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")

    async def _fake_run_doctor(*args: object, **kwargs: object) -> bool:
        _ = args, kwargs
        raise HealthServiceError(
            error_code="profile_not_found",
            reason="Profile not found: missing",
        )

    monkeypatch.setattr("afkbot.cli.commands.doctor._run_doctor", _fake_run_doctor)
    result = runner.invoke(app, ["doctor", "--profile", "missing"])

    assert result.exit_code == 2
    assert "Profile not found: missing" in result.stderr
