"""Tests for profile bootstrap CLI commands."""

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


def test_profile_bootstrap_set_show_list_and_clear(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Bootstrap CLI should manage profile-local prompt override files."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "analyst",
            "--name",
            "Analyst",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert add_result.exit_code == 0

    set_result = runner.invoke(
        app,
        [
            "profile",
            "bootstrap",
            "set",
            "analyst",
            "AGENTS.md",
            "--text",
            "You are the analyst agent.",
        ],
    )
    assert set_result.exit_code == 0
    set_payload = json.loads(set_result.stdout)
    assert set_payload["bootstrap"]["exists"] is True
    assert set_payload["bootstrap"]["content"] == "You are the analyst agent.\n"

    show_result = runner.invoke(app, ["profile", "bootstrap", "show", "analyst", "AGENTS.md"])
    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["bootstrap"]["path"] == "profiles/analyst/bootstrap/AGENTS.md"
    assert show_payload["bootstrap"]["content"] == "You are the analyst agent.\n"

    list_result = runner.invoke(app, ["profile", "bootstrap", "list", "analyst"])
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["bootstrap_files"][0]["file_name"] == "AGENTS.md"
    assert list_payload["bootstrap_files"][0]["exists"] is True

    clear_result = runner.invoke(app, ["profile", "bootstrap", "clear", "analyst", "AGENTS.md"])
    assert clear_result.exit_code == 0
    clear_payload = json.loads(clear_result.stdout)
    assert clear_payload["bootstrap"]["exists"] is False
    assert clear_payload["bootstrap"]["content"] is None
