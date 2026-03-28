"""Tests for profile binding CLI commands."""

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


def _add_profile(runner: CliRunner, profile_id: str, name: str) -> None:
    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            profile_id,
            "--name",
            name,
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert result.exit_code == 0


def test_profile_binding_set_list_resolve_show_and_delete(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Binding CLI should persist routing rules and resolve routing inputs."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    _add_profile(runner, "sales", "Sales")

    set_result = runner.invoke(
        app,
        [
            "profile",
            "binding",
            "set",
            "telegram-sales",
            "--transport",
            "telegram",
            "--profile-id",
            "sales",
            "--session-policy",
            "per-thread",
            "--peer-id",
            "42",
        ],
    )
    assert set_result.exit_code == 0
    set_payload = json.loads(set_result.stdout)
    assert set_payload["binding"]["binding_id"] == "telegram-sales"
    assert set_payload["binding"]["session_policy"] == "per-thread"

    show_result = runner.invoke(app, ["profile", "binding", "show", "telegram-sales"])
    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["binding"]["profile_id"] == "sales"

    list_result = runner.invoke(app, ["profile", "binding", "list", "--transport", "telegram"])
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert [item["binding_id"] for item in list_payload["bindings"]] == ["telegram-sales"]

    resolve_result = runner.invoke(
        app,
        [
            "profile",
            "binding",
            "resolve",
            "--transport",
            "telegram",
            "--peer-id",
            "42",
            "--thread-id",
            "9001",
        ],
    )
    assert resolve_result.exit_code == 0
    resolve_payload = json.loads(resolve_result.stdout)
    assert resolve_payload["decision"]["profile_id"] == "sales"
    assert resolve_payload["decision"]["session_id"] == "profile:sales:chat:42:thread:9001"

    delete_result = runner.invoke(app, ["profile", "binding", "delete", "telegram-sales"])
    assert delete_result.exit_code == 0
    delete_payload = json.loads(delete_result.stdout)
    assert delete_payload["ok"] is True
