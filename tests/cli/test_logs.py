"""CLI tests for log inspection commands."""

from __future__ import annotations

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.error_logging import component_log_path
from afkbot.settings import get_settings


def test_logs_command_shows_path_and_files(tmp_path, monkeypatch) -> None:
    """`afk logs` should work before setup and point users at useful files."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    log_path = component_log_path(get_settings(), "api")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("line one\nline two\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["logs"])

    get_settings.cache_clear()
    assert result.exit_code == 0
    assert f"Log directory: {tmp_path / 'logs'}" in result.stdout
    assert "api/errors.log" in result.stdout


def test_logs_tail_reads_requested_component(tmp_path, monkeypatch) -> None:
    """Operators should be able to print the newest lines for one component."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    log_path = component_log_path(get_settings(), "taskflow")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("old\nnew\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["logs", "tail", "--component", "taskflow", "--lines", "1"])

    get_settings.cache_clear()
    assert result.exit_code == 0
    assert f"File: {log_path}" in result.stdout
    tail_output = "\n".join(result.stdout.splitlines()[1:])
    assert "new" in tail_output
    assert "old" not in tail_output
