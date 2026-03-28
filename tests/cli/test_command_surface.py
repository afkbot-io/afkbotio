"""CLI command surface tests for public root groups."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from afkbot.cli.main import app


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
    assert "profile" in output
    assert "skill" in output
    assert "start" in output
    assert "automation" in output
    assert "subagent" in output
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

    # Assert
    assert result.exit_code == 0
    output = " ".join(result.stdout.split())
    assert "Use `afk start` to run the full local stack" in output
    assert "`afk bootstrap`" in output
    assert "`afk update`" in output
    assert "global system-prompt files" in output
    assert "`afk automation`" in output
    assert "`afk channel`" in output
    assert "`afk memory`" in output
    assert "`afk mcp`" in output
    assert "`afk skill` and `afk subagent`" in output
    assert "`afk browser install`" in output
    assert "browser automation runtime" in output
    assert "afk doctor" in output
