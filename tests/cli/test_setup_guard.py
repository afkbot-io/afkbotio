"""Tests for global setup-first guard in CLI entrypoint."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.browser_runtime import BrowserRuntimeStatus
from afkbot.services.setup.runtime_store import write_runtime_secrets
from afkbot.services.update_runtime import UpdateResult
from afkbot.settings import get_settings
from afkbot.version import CliVersionInfo


def _isolate_doctor_runtime(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        "afkbot.cli.commands.doctor.describe_managed_runtime_service",
        lambda: type("ServiceStatus", (), {"installed": False, "kind": None, "path": None})(),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.doctor.probe_runtime_stack",
        lambda *, host, runtime_port, api_port=None, timeout_sec=1.0: type(
            "StackProbe",
            (),
            {
                "running": False,
                "conflict": False,
                "runtime": type("Endpoint", (), {"ok": False, "url": f"http://{host}:{runtime_port}/healthz"})(),
                "api": type("Endpoint", (), {"ok": False, "url": f"http://{host}:{runtime_port + 1}/healthz"})(),
            },
        )(),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.doctor.is_runtime_port_pair_available",
        lambda *, host, runtime_port: True,
    )


def _prepare_guard_context(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    monkeypatch.delenv("AFKBOT_SKIP_SETUP_GUARD", raising=False)
    get_settings.cache_clear()


def test_cli_blocks_runtime_commands_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Commands except setup/uninstall should fail when setup marker is missing."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["chat", "--message", "hello"])

    # Assert
    assert result.exit_code == 1
    assert "Run 'afk setup' first." in result.stderr


def test_cli_allows_browser_commands_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Browser setup commands should be available before setup marker exists."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()
    monkeypatch.setattr(
        "afkbot.cli.commands.browser.get_browser_runtime_status",
        lambda settings: BrowserRuntimeStatus(
            ok=True,
            error_code=None,
            reason="ready",
            remediation=None,
        ),
    )

    # Act
    result = runner.invoke(app, ["browser", "status", "--json"])

    # Assert
    assert result.exit_code == 0


def test_cli_allows_bootstrap_commands_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Bootstrap prompt management should stay available before setup marker exists."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["bootstrap", "list"])

    # Assert
    assert result.exit_code == 0


def test_cli_allows_mcp_commands_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """MCP config management should stay available before setup marker exists."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["mcp", "list", "--profile", "default"])

    # Assert
    assert result.exit_code == 1


def test_cli_allows_plugin_commands_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Plugin install/list commands should remain available before setup marker exists."""

    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["plugin", "list"])

    assert result.exit_code == 0


def test_cli_allows_auth_commands_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """UI auth setup/status should stay available before setup marker exists."""

    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["auth", "status", "--json"])

    assert result.exit_code == 0


def test_cli_allows_upgrade_commands_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Upgrade runner should remain available before setup marker exists."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["upgrade", "apply", "--quiet"])

    # Assert
    assert result.exit_code == 0


def test_cli_allows_version_command_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Version command should stay available before setup marker exists."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()
    monkeypatch.setattr(
        "afkbot.cli.commands.version.load_cli_version_info",
        lambda: CliVersionInfo(version="1.2.3", git_sha="abc1234"),
    )

    # Act
    result = runner.invoke(app, ["version"])

    # Assert
    assert result.exit_code == 0
    assert result.stdout.strip() == "afk 1.2.3 (git abc1234)"


def test_cli_allows_update_commands_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Managed update should remain available before setup marker exists."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    runner = CliRunner()
    monkeypatch.setattr(
        "afkbot.cli.commands.update.run_update",
        lambda settings: UpdateResult(  # type: ignore[no-untyped-call]
            install_mode="host",
            source_updated=False,
            runtime_restarted=False,
            maintenance_applied=True,
            details=(),
        ),
    )

    # Act
    result = runner.invoke(app, ["update"])

    # Assert
    assert result.exit_code == 0


def test_cli_allows_runtime_commands_after_setup_marker(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Guard should allow runtime commands when setup marker exists."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    settings = get_settings()
    write_runtime_secrets(
        settings,
        secrets={"credentials_master_keys": Fernet.generate_key().decode("utf-8")},
    )
    get_settings.cache_clear()
    settings = get_settings()
    marker = settings.setup_state_path
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "version": 1,
                "completed": True,
                "installed_at": "2026-03-02T00:00:00+00:00",
                "config": {"llm_provider": "openrouter"},
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    settings.bootstrap_dir.mkdir(parents=True, exist_ok=True)
    for filename in settings.bootstrap_files:
        (settings.bootstrap_dir / filename).write_text("ok", encoding="utf-8")
    _isolate_doctor_runtime(monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["doctor", "--no-integrations", "--no-upgrades"])

    # Assert
    assert result.exit_code == 0
    assert "doctor: ok" in result.stdout


def test_cli_allows_runtime_commands_after_legacy_install_marker(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Guard should still allow runtime commands when only the legacy install marker exists."""

    # Arrange
    _prepare_guard_context(tmp_path, monkeypatch)
    settings = get_settings()
    legacy_marker = settings.root_dir / "profiles/.system/install_state.json"
    legacy_marker.parent.mkdir(parents=True, exist_ok=True)
    legacy_marker.write_text(
        json.dumps(
            {
                "version": 1,
                "completed": True,
                "installed_at": "2026-03-02T00:00:00+00:00",
                "config": {"llm_provider": "openrouter"},
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )
    settings.bootstrap_dir.mkdir(parents=True, exist_ok=True)
    for filename in settings.bootstrap_files:
        (settings.bootstrap_dir / filename).write_text("ok", encoding="utf-8")
    _isolate_doctor_runtime(monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["doctor", "--no-integrations"])

    # Assert
    assert result.exit_code == 1
    assert "upgrades: pending" in result.stdout
