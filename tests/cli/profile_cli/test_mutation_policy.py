"""Mutation and policy tests for profile CLI commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.settings import get_settings
from tests.cli.profile_cli._harness import _prepare_env


def _load_allowed_tools(*, profile_id: str) -> tuple[str, ...]:
    async def _run() -> tuple[str, ...]:
        settings = get_settings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        try:
            async with session_scope(factory) as session:
                row = await ProfilePolicyRepository(session).get(profile_id)
                assert row is not None
                return tuple(json.loads(row.allowed_tools_json))
        finally:
            await engine.dispose()

    get_settings.cache_clear()
    return asyncio.run(_run())


def test_profile_update_updates_history_memory_and_compaction(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`afk profile update` should update replay, memory, and compaction overrides."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    add_result = runner.invoke(
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
    assert add_result.exit_code == 0

    # Act
    set_result = runner.invoke(
        app,
        [
            "profile",
            "update",
            "default",
            "--yes",
            "--llm-history-turns",
            "16",
            "--memory-auto-search-enabled",
            "--memory-auto-search-scope-mode",
            "auto",
            "--memory-auto-search-limit",
            "4",
            "--memory-auto-search-global-limit",
            "3",
            "--memory-auto-context-item-chars",
            "180",
            "--memory-auto-save-enabled",
            "--memory-auto-save-scope-mode",
            "auto",
            "--memory-auto-save-kind",
            "preference",
            "--memory-auto-save-kind",
            "decision",
            "--memory-auto-save-max-chars",
            "900",
            "--session-compaction-enabled",
            "--session-compaction-trigger-turns",
            "16",
            "--session-compaction-keep-recent-turns",
            "8",
        ],
    )

    # Assert
    assert set_result.exit_code == 0
    payload = json.loads(set_result.stdout)
    assert payload["profile"]["effective_runtime"]["llm_history_turns"] == 16
    assert payload["profile"]["effective_runtime"]["memory_auto_search_enabled"] is True
    assert payload["profile"]["effective_runtime"]["memory_auto_search_scope_mode"] == "auto"
    assert payload["profile"]["effective_runtime"]["memory_auto_search_limit"] == 4
    assert payload["profile"]["effective_runtime"]["memory_auto_search_global_limit"] == 3
    assert payload["profile"]["effective_runtime"]["memory_auto_context_item_chars"] == 180
    assert payload["profile"]["effective_runtime"]["memory_auto_save_enabled"] is True
    assert payload["profile"]["effective_runtime"]["memory_auto_save_scope_mode"] == "auto"
    assert payload["profile"]["effective_runtime"]["memory_auto_save_kinds"] == ["preference", "decision"]
    assert payload["profile"]["effective_runtime"]["memory_auto_save_max_chars"] == 900
    assert payload["profile"]["effective_runtime"]["session_compaction_enabled"] is True
    assert payload["profile"]["effective_runtime"]["session_compaction_trigger_turns"] == 16
    assert payload["profile"]["effective_runtime"]["session_compaction_keep_recent_turns"] == 8


def test_profile_update_requests_managed_runtime_reload(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile updates should trigger one managed runtime reload hook."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        "afkbot.cli.commands.profile_update.reload_install_managed_runtime_notice",
        lambda settings: calls.append(str(settings.root_dir)),
    )
    runner = CliRunner()
    add_result = runner.invoke(
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
    assert add_result.exit_code == 0

    # Act
    update_result = runner.invoke(
        app,
        ["profile", "update", "default", "--yes", "--llm-history-turns", "16"],
    )

    # Assert
    assert update_result.exit_code == 0
    assert calls == [str(tmp_path)]


def test_profile_add_and_update_workspace_scope_modes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile CLI should persist explicit workspace scope selection and updates."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    add_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "ops",
            "--name",
            "Ops",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--policy-capability",
            "files",
            "--policy-file-access-mode",
            "read_only",
            "--policy-workspace-scope",
            "project_only",
        ],
    )
    assert add_result.exit_code == 0
    initial_show = runner.invoke(app, ["profile", "show", "ops", "--json"])
    assert initial_show.exit_code == 0
    initial_payload = json.loads(initial_show.stdout)
    assert initial_payload["effective_permissions"]["file_scope_mode"] == "project_only"

    update_result = runner.invoke(
        app,
        [
            "profile",
            "update",
            "ops",
            "--yes",
            "--policy-workspace-scope",
            "full_system",
        ],
    )
    updated_show = runner.invoke(app, ["profile", "show", "ops", "--json"])

    # Assert
    assert update_result.exit_code == 0
    assert updated_show.exit_code == 0
    updated_payload = json.loads(updated_show.stdout)
    assert updated_payload["effective_permissions"]["file_scope_mode"] == "full_system"
    assert updated_payload["profile"]["policy"]["allowed_directories"] == ["/"]


def test_profile_custom_workspace_scope_keeps_profile_root_and_custom_dirs(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Custom workspace scope should keep the profile workspace and add explicit custom roots."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    extra_root = tmp_path / "external"
    extra_root.mkdir(parents=True)

    # Act
    add_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "ops",
            "--name",
            "Ops",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--policy-capability",
            "files",
            "--policy-file-access-mode",
            "read_only",
            "--policy-workspace-scope",
            "custom",
            "--policy-allowed-dir",
            str(extra_root),
        ],
    )
    show_result = runner.invoke(app, ["profile", "show", "ops", "--json"])

    # Assert
    assert add_result.exit_code == 0
    assert show_result.exit_code == 0
    payload = json.loads(show_result.stdout)
    assert payload["effective_permissions"]["file_scope_mode"] == "custom"
    assert payload["effective_permissions"]["default_workspace_root"] == "profiles/ops"
    assert payload["effective_permissions"]["shell_default_cwd"] == "profiles/ops"
    assert set(payload["profile"]["policy"]["allowed_directories"]) == {
        str((tmp_path / "profiles/ops").resolve()),
        str(extra_root.resolve()),
    }


def test_profile_add_uses_safe_policy_defaults_for_new_profiles(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile creation should enable safe policy defaults without inheriting permissive network access."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "safe",
            "--name",
            "Safe",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )

    # Assert
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["profile"]["policy"]["enabled"] is True
    assert "*" not in payload["profile"]["policy"]["network_allowlist"]


def test_profile_add_resolves_session_job_run_for_shell_and_subagent_capabilities(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile add should persist session.job.run when shell or subagent capabilities are selected."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "ops",
            "--name",
            "Ops",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--policy-capability",
            "shell",
            "--policy-capability",
            "subagents",
        ],
    )

    assert result.exit_code == 0
    allowed_tools = _load_allowed_tools(profile_id="ops")
    assert "session.job.run" in allowed_tools
