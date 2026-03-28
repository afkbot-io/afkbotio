"""Tests for global bootstrap CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'profiles.db'}")
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()


def test_bootstrap_set_show_and_list(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Global bootstrap CLI should manage system-base prompt files."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    set_result = runner.invoke(
        app,
        [
            "bootstrap",
            "set",
            "AGENTS.md",
            "--text",
            "You are the global runtime agent.",
        ],
    )
    assert set_result.exit_code == 0
    set_payload = json.loads(set_result.stdout)
    assert set_payload["bootstrap"]["exists"] is True
    assert set_payload["bootstrap"]["content"] == "You are the global runtime agent.\n"

    show_result = runner.invoke(app, ["bootstrap", "show", "AGENTS.md"])
    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["bootstrap"]["path"] == "afkbot/bootstrap/AGENTS.md"
    assert show_payload["bootstrap"]["content"] == "You are the global runtime agent.\n"

    list_result = runner.invoke(app, ["bootstrap", "list"])
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["bootstrap_files"][0]["file_name"] == "AGENTS.md"
    assert list_payload["bootstrap_files"][0]["exists"] is True


def test_bootstrap_rejects_unknown_file_name(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Global bootstrap CLI should enforce configured bootstrap slots."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["bootstrap", "show", "MEMORY.md"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["error_code"] == "bootstrap_invalid_file"
