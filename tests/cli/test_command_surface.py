"""CLI command surface tests for public root groups."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings


@pytest.fixture(autouse=True)
def _skip_setup_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Command-surface help tests are about CLI exposure, not setup gating."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _normalize_text(value: str) -> str:
    """Collapse CLI help text into one stable whitespace-normalized line."""

    return " ".join(value.split())


def _strip_ansi(value: str) -> str:
    """Remove ANSI color/style escape sequences from CLI output."""

    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def test_legacy_groups_are_not_exposed() -> None:
    """Root help should not expose removed legacy command groups."""

    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["--help"])

    # Assert
    assert result.exit_code == 0
    output = result.stdout
    assert "chat" in output
    assert "browser" in output
    assert "channel" in output
    assert "connect" in output
    assert "credentials" in output
    assert "bootstrap" in output
    assert "setup" in output
    assert "uninstall" in output
    assert "update" in output
    assert "upgrade" in output
    assert "doctor" in output
    assert "memory" in output
    assert "mcp" in output
    assert "plugin" in output
    assert "profile" in output
    assert "skill" in output
    assert "start" in output
    assert "automation" in output
    assert "task" in output
    assert "subagent" in output
    assert "version" in output
    assert re.search(r"^\s*llm-set-provider\s", output, re.MULTILINE) is None
    assert re.search(r"^\s*llm-set-models\s", output, re.MULTILINE) is None
    assert re.search(r"^\s*migrate\s", output, re.MULTILINE) is None
    assert re.search(r"^\s*run\s", output, re.MULTILINE) is None
    assert re.search(r"^\s*api\s", output, re.MULTILINE) is None


def test_root_help_explains_primary_entrypoints() -> None:
    """Root help should point users to the unified runtime and chat entrypoints."""

    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["--help"])
    output = _normalize_text(_strip_ansi(result.stdout))

    # Assert
    assert result.exit_code == 0
    assert "Use `afk start` to run the full local stack" in output
    assert "`afk bootstrap`" in output
    assert "`afk update`" in output
    assert "global system-prompt files" in output
    assert "`afk automation`" in output
    assert "`afk task`" in output
    assert "`afk plugin`" in output
    assert "`afk channel`" in output
    assert "`afk memory`" in output
    assert "`afk mcp`" in output
    assert "`afk skill` and `afk subagent`" in output
    assert "`afk browser install`" in output
    assert "browser automation runtime" in output
    assert "afk doctor" in output


def test_task_help_exposes_dependency_and_run_management() -> None:
    """Task help should expose Task Flow dependency and run-history operator commands."""

    runner = CliRunner()

    result = runner.invoke(app, ["task", "--help"])

    assert result.exit_code == 0
    output = result.stdout
    assert "board" in output
    assert "comment-add" in output
    assert "comment-list" in output
    assert "inbox" in output
    assert "dependency-add" in output
    assert "dependency-list" in output
    assert "dependency-remove" in output
    assert "event-list" in output
    assert "review-list" in output
    assert "review-approve" in output
    assert "review-request-changes" in output
    assert "run-list" in output
    assert "run-get" in output
    assert "stale-list" in output
    assert "stale-sweep" in output



def test_plugin_help_exposes_install_lifecycle() -> None:
    """Plugin CLI help should expose install/inspect/lifecycle commands."""

    runner = CliRunner()
    result = runner.invoke(app, ["plugin", "--help"])

    assert result.exit_code == 0
    output = result.stdout
    assert "install" in output
    assert "scaffold" in output
    assert "list" in output
    assert "inspect" in output
    assert "config-get" in output
    assert "config-set" in output
    assert "config-reset" in output
    assert "update" in output
    assert "enable" in output
    assert "disable" in output
    assert "remove" in output
