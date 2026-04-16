"""Tests for Task Flow CLI command behavior."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings


def test_task_create_accepts_legacy_prompt_flag(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Legacy --prompt should map into description for backward compatibility."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    captured: dict[str, object] = {}

    async def _fake_create_task_payload(**kwargs: object) -> str:
        captured.update(kwargs)
        return json.dumps({"ok": True, "payload": {"task": {"id": "task_legacy"}}})

    monkeypatch.setattr("afkbot.cli.commands.task.create_task_payload", _fake_create_task_payload)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "create",
            "--title",
            "Legacy create",
            "--prompt",
            "legacy prompt body",
        ],
    )

    assert result.exit_code == 0
    assert captured["description"] == "legacy prompt body"
    assert captured["status"] == "todo"


def test_task_create_prefers_description_when_both_flags_provided(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """When both aliases are provided, --description should win deterministically."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    captured: dict[str, object] = {}

    async def _fake_create_task_payload(**kwargs: object) -> str:
        captured.update(kwargs)
        return json.dumps({"ok": True, "payload": {"task": {"id": "task_both"}}})

    monkeypatch.setattr("afkbot.cli.commands.task.create_task_payload", _fake_create_task_payload)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "create",
            "--title",
            "Dual flags",
            "--prompt",
            "legacy value",
            "--description",
            "new value",
        ],
    )

    assert result.exit_code == 0
    assert captured["description"] == "new value"


def test_task_create_help_mentions_prompt_alias(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Help output should document transition alias availability."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    runner = CliRunner()
    result = runner.invoke(app, ["task", "create", "--help"])

    assert result.exit_code == 0
    assert "--prompt" in result.stdout

    get_settings.cache_clear()

