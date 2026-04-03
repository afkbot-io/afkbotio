"""Tests for automation CLI commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.commands.automation import register as register_automation_commands
from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.automations.webhook_tokens import build_webhook_path, build_webhook_url
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
    asyncio.run(_ensure_default_profile())


def _build_automation_cli() -> typer.Typer:
    app = typer.Typer(no_args_is_help=True)
    register_automation_commands(app)
    return app


async def _ensure_default_profile() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    session_factory = create_session_factory(engine)
    try:
        async with session_scope(session_factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
    finally:
        await engine.dispose()


def test_automation_cli_crud_and_token_rotation(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Automation CLI should manage webhook automation CRUD and token rotation."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    app = _build_automation_cli()

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
        ],
    )
    assert create_result.exit_code == 0
    created = json.loads(create_result.stdout)
    automation = created["automation"]
    automation_id = int(automation["id"])
    assert automation["trigger_type"] == "webhook"
    assert isinstance(automation["webhook"]["webhook_token"], str)
    assert automation["webhook"]["webhook_path"] == build_webhook_path(
        "default",
        automation["webhook"]["webhook_token"],
    )
    assert automation["webhook"]["webhook_url"] == build_webhook_url(
        "http://127.0.0.1:8080",
        "default",
        automation["webhook"]["webhook_token"],
    )
    assert automation["webhook"]["last_execution_status"] == "idle"
    assert automation["webhook"]["last_session_id"] is None
    assert automation["webhook"]["last_started_at"] is None
    assert automation["webhook"]["last_succeeded_at"] is None
    assert automation["webhook"]["last_failed_at"] is None
    assert automation["webhook"]["last_error"] is None
    assert automation["webhook"]["last_event_hash"] is None
    assert automation["webhook"]["chat_resume_command"] is None
    created_token = automation["webhook"]["webhook_token"]

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
    assert shown["automation"]["id"] == automation_id
    assert shown["automation"]["webhook"]["webhook_token"] == created_token
    assert shown["automation"]["webhook"]["webhook_url"] == build_webhook_url(
        "http://127.0.0.1:8080",
        "default",
        created_token,
    )
    assert shown["automation"]["webhook"]["last_execution_status"] == "idle"

    get_result = runner.invoke(
        app,
        ["automation", "get", str(automation_id), "--profile", "default"],
    )
    assert get_result.exit_code == 0
    gotten = json.loads(get_result.stdout)
    assert gotten["automation"]["webhook"]["webhook_token"] == created_token
    assert gotten["automation"]["webhook"]["webhook_url"] == build_webhook_url(
        "http://127.0.0.1:8080",
        "default",
        created_token,
    )
    assert gotten["automation"]["webhook"]["last_execution_status"] == "idle"

    update_result = runner.invoke(
        app,
        [
            "automation",
            "update",
            str(automation_id),
            "--profile",
            "default",
            "--name",
            "Notify sales (updated)",
            "--status",
            "paused",
        ],
    )
    assert update_result.exit_code == 0
    updated = json.loads(update_result.stdout)
    assert updated["automation"]["name"] == "Notify sales (updated)"
    assert updated["automation"]["status"] == "paused"

    rotate_result = runner.invoke(
        app,
        [
            "automation",
            "update",
            str(automation_id),
            "--profile",
            "default",
            "--rotate-webhook-token",
        ],
    )
    assert rotate_result.exit_code == 0
    rotated = json.loads(rotate_result.stdout)
    assert rotated["automation"]["webhook"]["webhook_path"] == build_webhook_path(
        "default",
        rotated["automation"]["webhook"]["webhook_token"],
    )
    assert isinstance(rotated["automation"]["webhook"]["webhook_token"], str)
    assert rotated["automation"]["webhook"]["webhook_token"] != created_token
    assert rotated["automation"]["webhook"]["webhook_url"] == build_webhook_url(
        "http://127.0.0.1:8080",
        "default",
        rotated["automation"]["webhook"]["webhook_token"],
    )
    assert rotated["automation"]["webhook"]["last_execution_status"] == "idle"

    delete_result = runner.invoke(
        app,
        ["automation", "delete", str(automation_id), "--profile", "default"],
    )
    assert delete_result.exit_code == 0
    deleted = json.loads(delete_result.stdout)
    assert deleted == {"deleted": True, "id": automation_id}


def test_automation_cli_supports_manual_cron_tick(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Manual cron tick should expose triggered ids for due automations."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    app = _build_automation_cli()

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


def test_automation_cli_accepts_group_level_profile_option(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Automation CLI should accept --profile before the subcommand."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    app = _build_automation_cli()

    create_result = runner.invoke(
        app,
        [
            "automation",
            "--profile",
            "default",
            "create",
            "--name",
            "Group profile webhook",
            "--prompt",
            "Listen for events",
            "--trigger",
            "webhook",
        ],
    )
    assert create_result.exit_code == 0
    created = json.loads(create_result.stdout)
    automation_id = int(created["automation"]["id"])
    token = created["automation"]["webhook"]["webhook_token"]
    assert created["automation"]["webhook"]["webhook_url"] == build_webhook_url(
        "http://127.0.0.1:8080",
        "default",
        token,
    )

    list_result = runner.invoke(app, ["automation", "--profile", "default", "list"])
    assert list_result.exit_code == 0
    listed = json.loads(list_result.stdout)
    assert [item["id"] for item in listed["automations"]] == [automation_id]

    get_result = runner.invoke(
        app,
        ["automation", "--profile", "default", "get", str(automation_id)],
    )
    assert get_result.exit_code == 0
    gotten = json.loads(get_result.stdout)
    assert gotten["automation"]["webhook"]["webhook_token"] == token
