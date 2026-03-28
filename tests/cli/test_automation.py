"""Tests for automation CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.commands.automation import _build_delivery_target
from afkbot.cli.main import app
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'automations.db'}")
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()


def test_automation_cli_manages_delivery_defaults(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Automation CLI should CRUD persisted delivery target defaults."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    profile_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "default",
            "--name",
            "Default",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert profile_result.exit_code == 0

    create_result = runner.invoke(
        app,
        [
            "automation",
            "create",
            "--profile",
            "default",
            "--name",
            "Notify sales",
            "--prompt",
            "Send daily summary",
            "--trigger",
            "webhook",
            "--delivery-transport",
            "telegram",
            "--delivery-peer-id",
            "42",
            "--delivery-thread-id",
            "9001",
        ],
    )
    assert create_result.exit_code == 0
    created = json.loads(create_result.stdout)
    assert created["automation"]["delivery_mode"] == "target"
    assert created["automation"]["delivery_target"] == {
        "transport": "telegram",
        "binding_id": None,
        "account_id": None,
        "peer_id": "42",
        "thread_id": "9001",
        "user_id": None,
        "address": None,
        "subject": None,
    }
    automation_id = created["automation"]["id"]

    list_result = runner.invoke(app, ["automation", "list", "--profile", "default"])
    assert list_result.exit_code == 0
    listed = json.loads(list_result.stdout)
    assert [item["id"] for item in listed["automations"]] == [automation_id]

    show_result = runner.invoke(
        app,
        ["automation", "show", str(automation_id), "--profile", "default"],
    )
    assert show_result.exit_code == 0
    shown = json.loads(show_result.stdout)
    assert shown["automation"]["delivery_target"]["peer_id"] == "42"

    update_result = runner.invoke(
        app,
        [
            "automation",
            "update",
            str(automation_id),
            "--profile",
            "default",
            "--delivery-transport",
            "smtp",
            "--delivery-address",
            "ops@example.com",
            "--delivery-subject",
            "AFKBOT report",
        ],
    )
    assert update_result.exit_code == 0
    updated = json.loads(update_result.stdout)
    assert updated["automation"]["delivery_mode"] == "target"
    assert updated["automation"]["delivery_target"]["transport"] == "smtp"
    assert updated["automation"]["delivery_target"]["address"] == "ops@example.com"

    clear_result = runner.invoke(
        app,
        [
            "automation",
            "update",
            str(automation_id),
            "--profile",
            "default",
            "--clear-delivery-target",
        ],
    )
    assert clear_result.exit_code == 0
    cleared = json.loads(clear_result.stdout)
    assert cleared["automation"]["delivery_mode"] == "tool"
    assert cleared["automation"]["delivery_target"] is None

    delete_result = runner.invoke(
        app,
        ["automation", "delete", str(automation_id), "--profile", "default"],
    )
    assert delete_result.exit_code == 0
    deleted = json.loads(delete_result.stdout)
    assert deleted == {"deleted": True, "id": automation_id}


def test_build_delivery_target_binding_without_transport_raises_clean_error() -> None:
    """Binding-only delivery targets should keep model validation on missing transport."""

    with pytest.raises(Exception, match="transport is required") as exc_info:
        _build_delivery_target(
            transport=None,
            binding_id="binding-only",
            account_id=None,
            peer_id=None,
            thread_id=None,
            user_id=None,
            address=None,
            subject=None,
        )
    assert exc_info.value.__class__.__name__ == "BadParameter"


def test_automation_cli_supports_manual_cron_tick(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Manual cron tick should expose triggered ids for due automations."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    profile_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "default",
            "--name",
            "Default",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert profile_result.exit_code == 0

    create_result = runner.invoke(
        app,
        [
            "automation",
            "create",
            "--profile",
            "default",
            "--name",
            "Every five minutes",
            "--prompt",
            "ping",
            "--trigger",
            "cron",
            "--cron-expr",
            "*/5 * * * *",
            "--timezone",
            "UTC",
        ],
    )
    assert create_result.exit_code == 0
    created = json.loads(create_result.stdout)
    next_run_at = created["automation"]["cron"]["next_run_at"]
    assert next_run_at.startswith("20")

    tick_result = runner.invoke(
        app,
        [
            "automation",
            "cron-tick",
            "--now-utc",
            next_run_at,
        ],
    )
    assert tick_result.exit_code == 0
    payload = json.loads(tick_result.stdout)
    assert payload["cron_tick"]["triggered_ids"] == [created["automation"]["id"]]


def test_build_delivery_target_accepts_telegram_address_alias() -> None:
    """Telegram delivery target should treat generic address as one peer_id alias."""

    target = _build_delivery_target(
        transport="telegram",
        binding_id=None,
        account_id=None,
        peer_id=None,
        thread_id=None,
        user_id=None,
        address="-1001234567890",
        subject=None,
    )
    assert target is not None
    assert target.peer_id == "-1001234567890"
    assert target.address is None


def test_automation_cli_accepts_tool_delivery_mode_without_target(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """CLI create should support explicit tool delivery mode without target coordinates."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    profile_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "default",
            "--name",
            "Default",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert profile_result.exit_code == 0

    create_result = runner.invoke(
        app,
        [
            "automation",
            "create",
            "--profile",
            "default",
            "--name",
            "Tool send",
            "--prompt",
            "Use app.run to send Telegram message ПРИВЕТ.",
            "--trigger",
            "cron",
            "--cron-expr",
            "*/5 * * * *",
            "--delivery-mode",
            "tool",
        ],
    )
    assert create_result.exit_code == 0
    created = json.loads(create_result.stdout)
    assert created["automation"]["delivery_mode"] == "tool"
    assert "Execution hints:" in created["automation"]["prompt"]
    assert "app.run with the Telegram app" in created["automation"]["prompt"]
    assert created["automation"]["delivery_target"] is None
