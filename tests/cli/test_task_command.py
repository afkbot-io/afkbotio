"""Focused CLI tests for Task Flow commands."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings


def _strip_ansi(value: str) -> str:
    """Remove ANSI style sequences from rich Typer output."""

    return re.sub(r"\x1b\[[0-9;]*m", "", value)


def test_task_create_help_mentions_prompt_alias(monkeypatch) -> None:
    """Help output should document the transitional --prompt alias."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["task", "create", "--help"])

    assert result.exit_code == 0
    output = _strip_ansi(result.stdout)
    assert "--description" in output
    assert "--prompt" in output


def test_task_create_requires_description_or_prompt(monkeypatch) -> None:
    """Create should fail early when neither --description nor --prompt is provided."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["task", "create", "--title", "Missing body"])

    assert result.exit_code != 0
    output = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "--description" in output
    assert "--prompt" in output


def test_task_create_accepts_legacy_prompt_flag(monkeypatch) -> None:
    """Legacy --prompt should still map to description for compatibility."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    captured: dict[str, object] = {}

    async def _fake_create_task_payload(**kwargs):
        captured.update(kwargs)
        return "{\"task\":{\"id\":\"task_1\"}}"

    monkeypatch.setattr(module, "create_task_payload", _fake_create_task_payload)
    monkeypatch.setattr(module, "resolve_local_human_ref", lambda _settings: "cli_user:test")

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["task", "create", "--title", "Legacy", "--prompt", "Use old flag"],
    )

    assert result.exit_code == 0
    assert captured["description"] == "Use old flag"
    assert captured["status"] == "todo"


def test_task_create_prefers_description_over_prompt(monkeypatch) -> None:
    """Preferred --description should win deterministically over legacy --prompt."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    captured: dict[str, object] = {}

    async def _fake_create_task_payload(**kwargs):
        captured.update(kwargs)
        return "{\"task\":{\"id\":\"task_1\"}}"

    monkeypatch.setattr(module, "create_task_payload", _fake_create_task_payload)
    monkeypatch.setattr(module, "resolve_local_human_ref", lambda _settings: "cli_user:test")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "create",
            "--title",
            "Preferred",
            "--description",
            "Preferred text",
            "--prompt",
            "Legacy text",
        ],
    )

    assert result.exit_code == 0
    assert captured["description"] == "Preferred text"
