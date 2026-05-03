"""Tests for shell sandbox CLI commands."""

from __future__ import annotations

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.tools.shell_sandbox import ShellSandboxBackendStatus


def test_sandbox_status_json_reports_install_command(monkeypatch) -> None:
    """sandbox status should expose a deterministic preflight payload."""

    monkeypatch.setattr(
        "afkbot.cli.commands.sandbox.get_shell_sandbox_backend_status",
        lambda: ShellSandboxBackendStatus(
            ok=False,
            sandbox_kind="none",
            helper_path=None,
            reason="missing",
            install_command=("sudo", "apt-get", "install", "-y", "bubblewrap"),
        ),
    )
    runner = CliRunner()

    result = runner.invoke(app, ["sandbox", "status", "--json"])

    assert result.exit_code == 1
    assert '"ok": false' in result.stdout
    assert '"install_command": ["sudo", "apt-get", "install", "-y", "bubblewrap"]' in result.stdout
