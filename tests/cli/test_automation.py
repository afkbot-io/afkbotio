"""Tests for automation CLI commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cryptography.fernet import Fernet
import typer
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.commands.automation import register as register_automation_commands
from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.automations.graph.contracts import AutomationGraphNodeSpec, AutomationGraphSpec
from afkbot.services.automations.service import get_automations_service
from afkbot.services.automations.webhook_tokens import build_webhook_path, build_webhook_url
from afkbot.settings import get_settings


def _prepare_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    bootstrap_dir = tmp_path / "afkbot/bootstrap"
    bootstrap_dir.mkdir(parents=True)
    for file_name in ("AGENTS.md", "IDENTITY.md", "TOOLS.md", "SECURITY.md"):
        (bootstrap_dir / file_name).write_text(file_name, encoding="utf-8")
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'automations.db'}")
    monkeypatch.setenv("AFKBOT_CREDENTIALS_MASTER_KEYS", Fernet.generate_key().decode("utf-8"))
    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()
    asyncio.run(_ensure_default_profile())


def _build_automation_cli() -> typer.Typer:
    app = typer.Typer(no_args_is_help=True)
    register_automation_commands(app)
    return app


def _runtime_base_url() -> str:
    """Return the effective local runtime base URL for CLI automation tests."""

    settings = get_settings()
    return f"http://{settings.runtime_host}:{settings.runtime_port}"


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
        _runtime_base_url(),
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
    listed_webhook = listed["automations"][0]["webhook"]
    assert listed_webhook["webhook_token"] is None
    assert listed_webhook["webhook_path"] is None
    assert listed_webhook["webhook_url"] is None
    assert listed_webhook["webhook_token_masked"] == "[HIDDEN]"

    show_result = runner.invoke(
        app,
        ["automation", "show", str(automation_id), "--profile", "default"],
    )
    assert show_result.exit_code == 0
    shown = json.loads(show_result.stdout)
    assert shown["automation"]["id"] == automation_id
    assert shown["automation"]["webhook"]["webhook_token"] is None
    assert shown["automation"]["webhook"]["webhook_path"] is None
    assert shown["automation"]["webhook"]["webhook_url"] is None
    assert shown["automation"]["webhook"]["webhook_token_masked"] == "[HIDDEN]"
    assert shown["automation"]["webhook"]["last_execution_status"] == "idle"

    get_result = runner.invoke(
        app,
        ["automation", "get", str(automation_id), "--profile", "default"],
    )
    assert get_result.exit_code == 0
    gotten = json.loads(get_result.stdout)
    assert gotten["automation"]["webhook"]["webhook_token"] is None
    assert gotten["automation"]["webhook"]["webhook_path"] is None
    assert gotten["automation"]["webhook"]["webhook_url"] is None
    assert gotten["automation"]["webhook"]["webhook_token_masked"] == "[HIDDEN]"
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
        _runtime_base_url(),
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
        _runtime_base_url(),
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
    assert gotten["automation"]["webhook"]["webhook_token"] is None
    assert gotten["automation"]["webhook"]["webhook_path"] is None
    assert gotten["automation"]["webhook"]["webhook_url"] is None


def test_automation_cli_exposes_graph_inspection_commands(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Automation CLI should expose graph and run inspection payloads."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    app = _build_automation_cli()
    settings = get_settings()
    service = get_automations_service(settings)
    created = asyncio.run(
        service.create_webhook(
            profile_id="default",
            name="Graph CLI",
            prompt="fallback prompt",
            execution_mode="graph",
        )
    )
    asyncio.run(
        service.apply_graph(
            profile_id="default",
            automation_id=created.id,
            spec=AutomationGraphSpec(
                name="cli-graph",
                nodes=[
                    AutomationGraphNodeSpec(
                        key="trigger",
                        name="Trigger",
                        node_kind="builtin",
                        node_type="trigger.input",
                    ),
                    AutomationGraphNodeSpec(
                        key="finish",
                        name="Finish",
                        node_kind="builtin",
                        node_type="passthrough",
                    ),
                ],
                edges=[{"source_key": "trigger", "target_key": "finish"}],
            ),
        )
    )
    assert created.webhook is not None
    assert created.webhook.webhook_token is not None
    asyncio.run(
        service.trigger_webhook(
            profile_id="default",
            token=created.webhook.webhook_token,
            payload={"event_id": "evt-cli"},
        )
    )
    run = asyncio.run(service.list_graph_runs(profile_id="default", automation_id=created.id))[0]

    graph_result = runner.invoke(
        app,
        ["automation", "graph-show", str(created.id), "--profile", "default"],
    )
    assert graph_result.exit_code == 0
    assert json.loads(graph_result.stdout)["graph"]["automation_id"] == created.id

    run_result = runner.invoke(
        app,
        ["automation", "run-show", str(run.id), "--profile", "default"],
    )
    assert run_result.exit_code == 0
    assert json.loads(run_result.stdout)["run"]["id"] == run.id

    trace_result = runner.invoke(
        app,
        ["automation", "trace", str(run.id), "--profile", "default"],
    )
    assert trace_result.exit_code == 0
    trace_data = json.loads(trace_result.stdout)
    assert trace_data["trace"]["run"]["id"] == run.id
    assert [item["node_key"] for item in trace_data["trace"]["nodes"]] == ["trigger", "finish"]


def test_automation_cli_graph_apply_persists_spec_and_graph_show_reads_it(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """graph-apply should persist the spec and graph-show should read the same active flow."""

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
            "Graph Apply",
            "--prompt",
            "fallback prompt",
            "--trigger",
            "webhook",
            "--mode",
            "graph",
        ],
    )
    assert create_result.exit_code == 0
    automation_id = int(json.loads(create_result.stdout)["automation"]["id"])

    spec_json = json.dumps(
        {
            "name": "cli-apply",
            "nodes": [
                {
                    "key": "trigger",
                    "name": "Trigger",
                    "node_kind": "builtin",
                    "node_type": "trigger.input",
                },
                {
                    "key": "finish",
                    "name": "Finish",
                    "node_kind": "builtin",
                    "node_type": "passthrough",
                },
            ],
            "edges": [{"source_key": "trigger", "target_key": "finish"}],
        }
    )

    apply_result = runner.invoke(
        app,
        [
            "automation",
            "graph-apply",
            str(automation_id),
            "--profile",
            "default",
            "--spec-json",
            spec_json,
        ],
    )
    assert apply_result.exit_code == 0
    graph_payload = json.loads(apply_result.stdout)["graph"]
    assert graph_payload["automation_id"] == automation_id
    assert [item["key"] for item in graph_payload["nodes"]] == ["trigger", "finish"]

    show_result = runner.invoke(
        app,
        ["automation", "graph-show", str(automation_id), "--profile", "default"],
    )
    assert show_result.exit_code == 0
    shown_graph = json.loads(show_result.stdout)["graph"]
    assert shown_graph["name"] == "cli-apply"
    assert [item["key"] for item in shown_graph["nodes"]] == ["trigger", "finish"]


def test_automation_cli_graph_apply_rejects_invalid_spec(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """graph-apply should fail fast on invalid specs instead of persisting broken flows."""

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
            "Graph Invalid",
            "--prompt",
            "fallback prompt",
            "--trigger",
            "webhook",
            "--mode",
            "graph",
        ],
    )
    assert create_result.exit_code == 0
    automation_id = int(json.loads(create_result.stdout)["automation"]["id"])

    invalid_spec_json = json.dumps(
        {
            "name": "cli-invalid",
            "nodes": [
                {
                    "key": "delegate",
                    "name": "Delegate",
                    "node_kind": "agent",
                    "node_type": "subagent.run",
                    "config": {},
                }
            ],
            "edges": [],
        }
    )

    apply_result = runner.invoke(
        app,
        [
            "automation",
            "graph-apply",
            str(automation_id),
            "--profile",
            "default",
            "--spec-json",
            invalid_spec_json,
        ],
    )
    assert apply_result.exit_code == 1
    error_payload = json.loads(apply_result.stdout)
    assert error_payload["ok"] is False
    assert error_payload["error_code"] == "invalid_graph_spec"
    assert "requires config.prompt" in error_payload["reason"]


def test_automation_cli_graph_apply_rejects_invalid_task_and_action_specs(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """graph-apply should reject broken task/action node contracts before persisting flows."""

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
            "Graph Invalid Task Action",
            "--prompt",
            "fallback prompt",
            "--trigger",
            "webhook",
            "--mode",
            "graph",
        ],
    )
    assert create_result.exit_code == 0
    automation_id = int(json.loads(create_result.stdout)["automation"]["id"])

    invalid_spec_json = json.dumps(
        {
            "name": "cli-invalid-task-action",
            "nodes": [
                {
                    "key": "trigger",
                    "name": "Trigger",
                    "node_kind": "builtin",
                    "node_type": "trigger.input",
                    "config": {},
                },
                {
                    "key": "create_task",
                    "name": "Create Task",
                    "node_kind": "task",
                    "node_type": "task.create",
                    "config": {"description_path": "default.event_id"},
                },
                {
                    "key": "call_app",
                    "name": "Call App",
                    "node_kind": "action",
                    "node_type": "app.run",
                    "config": {"app_name": "demo"},
                },
            ],
            "edges": [],
        }
    )

    apply_result = runner.invoke(
        app,
        [
            "automation",
            "graph-apply",
            str(automation_id),
            "--profile",
            "default",
            "--spec-json",
            invalid_spec_json,
        ],
    )
    assert apply_result.exit_code == 1
    error_payload = json.loads(apply_result.stdout)
    assert error_payload["ok"] is False
    assert error_payload["error_code"] == "invalid_graph_spec"
    assert "task.create node `create_task` requires config.title or config.title_path" in error_payload["reason"]
    assert "action app.run node `call_app` requires config.action" in error_payload["reason"]


def test_automation_cli_graph_apply_rejects_task_session_binding_overrides(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """graph-apply should reject task.create specs that try to override session binding."""

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
            "Graph Invalid Task Session",
            "--prompt",
            "fallback prompt",
            "--trigger",
            "webhook",
            "--mode",
            "graph",
        ],
    )
    assert create_result.exit_code == 0
    automation_id = int(json.loads(create_result.stdout)["automation"]["id"])

    invalid_spec_json = json.dumps(
        {
            "name": "cli-invalid-task-session",
            "nodes": [
                {
                    "key": "trigger",
                    "name": "Trigger",
                    "node_kind": "builtin",
                    "node_type": "trigger.input",
                    "config": {},
                },
                {
                    "key": "create_task",
                    "name": "Create Task",
                    "node_kind": "task",
                    "node_type": "task.create",
                    "config": {
                        "title": "Process webhook",
                        "description": "body",
                        "session_id": "chat:borrowed",
                    },
                },
            ],
            "edges": [],
        }
    )

    apply_result = runner.invoke(
        app,
        [
            "automation",
            "graph-apply",
            str(automation_id),
            "--profile",
            "default",
            "--spec-json",
            invalid_spec_json,
        ],
    )
    assert apply_result.exit_code == 1
    error_payload = json.loads(apply_result.stdout)
    assert error_payload["ok"] is False
    assert error_payload["error_code"] == "invalid_graph_spec"
    assert "task.create node `create_task` does not support config.session_id" in error_payload["reason"]


def test_automation_cli_graph_apply_rejects_blocked_tool_run_specs(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """graph-apply should reject unsupported control-plane generic tool nodes."""

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
            "Graph Invalid Tool Run",
            "--prompt",
            "fallback prompt",
            "--trigger",
            "webhook",
            "--mode",
            "graph",
        ],
    )
    assert create_result.exit_code == 0
    automation_id = int(json.loads(create_result.stdout)["automation"]["id"])

    invalid_spec_json = json.dumps(
        {
            "name": "cli-invalid-tool-run",
            "nodes": [
                {
                    "key": "trigger",
                    "name": "Trigger",
                    "node_kind": "builtin",
                    "node_type": "trigger.input",
                    "config": {},
                },
                {
                    "key": "call_tool",
                    "name": "Call Tool",
                    "node_kind": "action",
                    "node_type": "tool.run",
                    "config": {"tool_name": "subagent.profile.get"},
                },
            ],
            "edges": [],
        }
    )

    apply_result = runner.invoke(
        app,
        [
            "automation",
            "graph-apply",
            str(automation_id),
            "--profile",
            "default",
            "--spec-json",
            invalid_spec_json,
        ],
    )
    assert apply_result.exit_code == 1
    error_payload = json.loads(apply_result.stdout)
    assert error_payload["ok"] is False
    assert error_payload["error_code"] == "invalid_graph_spec"
    assert "action tool.run node `call_tool` supports only curated automation data-plane tools" in error_payload["reason"]
    assert "subagent.profile.get" in error_payload["reason"]


def test_automation_cli_graph_apply_rejects_tool_run_app_run(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """graph-apply should force app integrations through dedicated action.app.run nodes."""

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
            "Graph Invalid App Tool Run",
            "--prompt",
            "fallback prompt",
            "--trigger",
            "webhook",
            "--mode",
            "graph",
        ],
    )
    assert create_result.exit_code == 0
    automation_id = int(json.loads(create_result.stdout)["automation"]["id"])

    invalid_spec_json = json.dumps(
        {
            "name": "cli-invalid-app-tool-run",
            "nodes": [
                {
                    "key": "call_tool",
                    "name": "Call Tool",
                    "node_kind": "action",
                    "node_type": "tool.run",
                    "config": {"tool_name": "app.run"},
                },
            ],
            "edges": [],
        }
    )

    apply_result = runner.invoke(
        app,
        [
            "automation",
            "graph-apply",
            str(automation_id),
            "--profile",
            "default",
            "--spec-json",
            invalid_spec_json,
        ],
    )
    assert apply_result.exit_code == 1
    error_payload = json.loads(apply_result.stdout)
    assert error_payload["ok"] is False
    assert error_payload["error_code"] == "invalid_graph_spec"
    assert "does not allow tool `app.run`; use dedicated node_type `app.run` instead" in error_payload["reason"]


def test_automation_cli_rejects_unimplemented_branch_error_only_mode(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """CLI should not allow creating automations with unsupported fallback modes."""

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
            "Invalid fallback",
            "--prompt",
            "fallback prompt",
            "--trigger",
            "webhook",
            "--graph-fallback-mode",
            "branch_error_only",
        ],
    )
    assert create_result.exit_code == 1
    payload = json.loads(create_result.stdout)
    assert payload["ok"] is False
    assert payload["error_code"] == "invalid_graph_fallback_mode"
