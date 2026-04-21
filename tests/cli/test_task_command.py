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


def test_task_create_supports_structured_owner_and_reviewer_inputs(monkeypatch) -> None:
    """Create should normalize structured profile/subagent selectors for owners and reviewers."""

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
            "Structured",
            "--description",
            "Route work through structured selectors.",
            "--owner-profile",
            "papercliper",
            "--owner-subagent",
            "researcher",
            "--reviewer-profile",
            "analyst",
        ],
    )

    assert result.exit_code == 0
    assert captured["owner_type"] == "ai_subagent"
    assert captured["owner_ref"] == "papercliper:researcher"
    assert captured["reviewer_type"] == "ai_profile"
    assert captured["reviewer_ref"] == "analyst"


def test_task_create_rejects_conflicting_raw_and_structured_owner_inputs(monkeypatch) -> None:
    """Create should fail early on ambiguous raw+structured owner selectors."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    async def _unexpected_create_task_payload(**kwargs):
        raise AssertionError(f"create_task_payload should not be called: {kwargs}")

    monkeypatch.setattr(module, "create_task_payload", _unexpected_create_task_payload)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "create",
            "--title",
            "Conflict",
            "--description",
            "Do not accept ambiguous owner selectors.",
            "--owner-ref",
            "analyst",
            "--owner-profile",
            "papercliper",
        ],
    )

    assert result.exit_code != 0
    output = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "cannot be combined" in output


def test_task_update_supports_structured_owner_and_reviewer_inputs(monkeypatch) -> None:
    """Update should normalize structured profile/subagent selectors for owners and reviewers."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    captured: dict[str, object] = {}

    async def _fake_update_task_payload(**kwargs):
        captured.update(kwargs)
        return "{\"task\":{\"id\":\"task_1\"}}"

    monkeypatch.setattr(module, "update_task_payload", _fake_update_task_payload)
    monkeypatch.setattr(module, "resolve_local_human_ref", lambda _settings: "cli_user:test")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "update",
            "task_1",
            "--owner-profile",
            "papercliper",
            "--owner-subagent",
            "reviewer",
            "--reviewer-profile",
            "analyst",
        ],
    )

    assert result.exit_code == 0
    assert captured["owner_type"] == "ai_subagent"
    assert captured["owner_ref"] == "papercliper:reviewer"
    assert captured["reviewer_type"] == "ai_profile"
    assert captured["reviewer_ref"] == "analyst"


def test_task_list_supports_structured_owner_filter(monkeypatch) -> None:
    """List should normalize structured owner filters before hitting the CLI payload layer."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    captured: dict[str, object] = {}

    async def _fake_list_tasks_payload(**kwargs):
        captured.update(kwargs)
        return "{\"tasks\":[]}"

    monkeypatch.setattr(module, "list_tasks_payload", _fake_list_tasks_payload)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "list",
            "--owner-profile",
            "papercliper",
            "--owner-subagent",
            "reviewer",
        ],
    )

    assert result.exit_code == 0
    assert captured["owner_type"] == "ai_subagent"
    assert captured["owner_ref"] == "papercliper:reviewer"


def test_task_flow_create_supports_structured_default_owner_profile(monkeypatch) -> None:
    """Flow create should normalize structured profile-only default owner selectors."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    captured: dict[str, object] = {}

    async def _fake_create_flow_payload(**kwargs):
        captured.update(kwargs)
        return "{\"task_flow\":{\"id\":\"flow_1\"}}"

    monkeypatch.setattr(module, "create_flow_payload", _fake_create_flow_payload)
    monkeypatch.setattr(module, "resolve_local_human_ref", lambda _settings: "cli_user:test")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "flow-create",
            "--title",
            "Structured flow",
            "--default-owner-profile",
            "analyst",
        ],
    )

    assert result.exit_code == 0
    assert captured["default_owner_type"] == "ai_profile"
    assert captured["default_owner_ref"] == "analyst"


def test_task_review_list_supports_structured_actor_inputs(monkeypatch) -> None:
    """Review list should normalize structured reviewer selectors for ai_subagent inboxes."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    captured: dict[str, object] = {}

    async def _fake_list_review_tasks_payload(**kwargs):
        captured.update(kwargs)
        return "{\"review_tasks\":[]}"

    monkeypatch.setattr(module, "list_review_tasks_payload", _fake_list_review_tasks_payload)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "review-list",
            "--actor-profile",
            "papercliper",
            "--actor-subagent",
            "reviewer",
        ],
    )

    assert result.exit_code == 0
    assert captured["actor_type"] == "ai_subagent"
    assert captured["actor_ref"] == "papercliper:reviewer"


def test_task_review_list_rejects_explicit_human_actor_with_structured_ai_selector(monkeypatch) -> None:
    """Explicit human actor type should not be silently reinterpreted as an AI selector."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    async def _unexpected_list_review_tasks_payload(**kwargs):
        raise AssertionError(f"list_review_tasks_payload should not be called: {kwargs}")

    monkeypatch.setattr(module, "list_review_tasks_payload", _unexpected_list_review_tasks_payload)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "review-list",
            "--actor-type",
            "human",
            "--actor-profile",
            "papercliper",
            "--actor-subagent",
            "reviewer",
        ],
    )

    assert result.exit_code != 0
    output = _strip_ansi(result.stdout + (result.stderr or ""))
    assert "require actor_type=ai_subagent" in output


def test_task_review_request_changes_supports_structured_actor_and_owner_inputs(monkeypatch) -> None:
    """Review request-changes should normalize structured actor and reassignment selectors."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    captured: dict[str, object] = {}

    async def _fake_request_review_changes_payload(**kwargs):
        captured.update(kwargs)
        return "{\"task\":{\"id\":\"task_1\"}}"

    monkeypatch.setattr(module, "request_review_changes_payload", _fake_request_review_changes_payload)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "review-request-changes",
            "task_1",
            "--reason-text",
            "Needs fixes",
            "--actor-profile",
            "papercliper",
            "--actor-subagent",
            "reviewer",
            "--owner-profile",
            "analyst",
        ],
    )

    assert result.exit_code == 0
    assert captured["actor_type"] == "ai_subagent"
    assert captured["actor_ref"] == "papercliper:reviewer"
    assert captured["owner_type"] == "ai_profile"
    assert captured["owner_ref"] == "analyst"


def test_task_stale_sweep_supports_structured_owner_filter(monkeypatch) -> None:
    """Stale sweep should normalize structured executor filters before invoking repair."""

    monkeypatch.setenv("AFKBOT_SKIP_SETUP_GUARD", "1")
    get_settings.cache_clear()

    from afkbot.cli.commands import task as module

    captured: dict[str, object] = {}

    async def _fake_sweep_stale_task_claims_payload(**kwargs):
        captured.update(kwargs)
        return "{\"maintenance\":{\"repaired_count\":0}}"

    monkeypatch.setattr(module, "sweep_stale_task_claims_payload", _fake_sweep_stale_task_claims_payload)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "task",
            "stale-sweep",
            "--owner-profile",
            "papercliper",
            "--owner-subagent",
            "reviewer",
        ],
    )

    assert result.exit_code == 0
    assert captured["owner_ref"] == "papercliper:reviewer"
