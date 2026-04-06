"""Tests for `afk version` CLI command."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.version import CliVersionInfo, load_cli_version_info


def test_version_command_renders_package_and_git_checkout_metadata(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Version command should print enough metadata to identify the running checkout."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setattr(
        "afkbot.cli.commands.version.load_cli_version_info",
        lambda: CliVersionInfo(
            version="1.2.3",
            git_sha="abc1234",
            git_branch="codex/fix-chat-transcript-tail",
            dirty=True,
        ),
    )

    # Act
    result = runner.invoke(app, ["version"])

    # Assert
    assert result.exit_code == 0
    assert (
        result.stdout.strip()
        == "afk 1.2.3 (git abc1234, on codex/fix-chat-transcript-tail, dirty)"
    )


def test_version_command_falls_back_to_plain_package_version(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When git metadata is unavailable, version command should still print package version."""

    # Arrange
    runner = CliRunner()
    monkeypatch.setattr(
        "afkbot.cli.commands.version.load_cli_version_info",
        lambda: CliVersionInfo(version="1.2.3"),
    )

    # Act
    result = runner.invoke(app, ["version"])

    # Assert
    assert result.exit_code == 0
    assert result.stdout.strip() == "afk 1.2.3"


def test_load_cli_version_info_prefers_local_checkout_version(
    tmp_path: Path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Local source checkouts should report their own pyproject version over installed metadata."""

    # Arrange
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='afkbotio'\nversion='1.0.6'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda _name: "9.9.9",
    )

    # Act
    info = load_cli_version_info(root_dir=tmp_path)

    # Assert
    assert info.version == "1.0.6"
