"""Surface and presentation tests for profile CLI commands."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channels.endpoint_contracts import TelegramPollingEndpointConfig
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.settings import get_settings
from tests.cli.profile_cli._harness import _prepare_env


def test_profile_add_show_and_list(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Profile CLI should create a profile and expose it via list/show commands."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
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
            "--thinking-level",
            "very-high",
            "--llm-history-turns",
            "18",
            "--planning-mode",
            "on",
            "--policy-preset",
            "strict",
            "--policy-capability",
            "files",
            "--tool-plugin",
            "debug_echo",
            "--session-compaction-enabled",
            "--session-compaction-trigger-turns",
            "14",
            "--session-compaction-keep-recent-turns",
            "7",
            "--session-compaction-prune-raw-turns",
        ],
    )
    show_result = runner.invoke(app, ["profile", "show", "analyst", "--json"])
    list_result = runner.invoke(app, ["profile", "list", "--json"])

    # Assert
    assert add_result.exit_code == 0
    add_payload = json.loads(add_result.stdout)
    assert add_payload["profile"]["id"] == "analyst"
    assert add_payload["profile"]["effective_runtime"]["llm_provider"] == "openai"
    assert add_payload["profile"]["effective_runtime"]["llm_thinking_level"] == "very_high"
    assert add_payload["profile"]["effective_runtime"]["llm_history_turns"] == 18
    assert add_payload["profile"]["effective_runtime"]["chat_planning_mode"] == "on"
    assert add_payload["profile"]["effective_runtime"]["chat_secret_guard_enabled"] is False
    assert add_payload["profile"]["effective_runtime"]["enabled_tool_plugins"] == ["debug_echo"]
    assert add_payload["profile"]["effective_runtime"]["memory_auto_search_scope_mode"] == "auto"
    assert add_payload["profile"]["effective_runtime"]["memory_auto_save_scope_mode"] == "auto"
    assert add_payload["profile"]["effective_runtime"]["session_compaction_enabled"] is True
    assert add_payload["profile"]["effective_runtime"]["session_compaction_trigger_turns"] == 14
    assert add_payload["profile"]["effective_runtime"]["session_compaction_keep_recent_turns"] == 7
    assert add_payload["profile"]["effective_runtime"]["session_compaction_prune_raw_turns"] is True

    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["profile"]["profile_root"] == "profiles/analyst"
    assert show_payload["profile"]["system_dir"] == "profiles/analyst/.system"
    assert show_payload["profile"]["runtime_config"]["llm_model"] == "gpt-4o-mini"
    assert show_payload["profile"]["runtime_config"]["llm_thinking_level"] == "very_high"
    assert show_payload["profile"]["runtime_config"]["llm_history_turns"] == 18
    assert show_payload["profile"]["runtime_config"]["chat_planning_mode"] == "on"
    assert show_payload["profile"]["effective_runtime"]["chat_secret_guard_enabled"] is False
    assert show_payload["profile"]["effective_runtime"]["memory_auto_search_scope_mode"] == "auto"
    assert show_payload["profile"]["effective_runtime"]["memory_auto_save_scope_mode"] == "auto"
    assert show_payload["profile"]["runtime_config"]["session_compaction_enabled"] is True
    assert show_payload["profile"]["runtime_config"]["session_compaction_prune_raw_turns"] is True
    assert show_payload["profile"]["runtime_config_path"] == "profiles/analyst/.system/agent_config.json"
    assert show_payload["profile"]["bootstrap_dir"] == "profiles/analyst/bootstrap"
    assert show_payload["profile"]["skills_dir"] == "profiles/analyst/skills"
    assert show_payload["profile"]["subagents_dir"] == "profiles/analyst/subagents"
    assert show_payload["mutation_state"]["merge_order"] == [
        "explicit_cli_overrides",
        "persisted_current_values",
        "inherited_defaults",
        "system_defaults",
    ]
    assert (
        show_payload["mutation_state"]["inherited_defaults_source"]
        == "global runtime settings and setup defaults"
    )
    assert "runtime.llm_provider" in show_payload["mutation_state"]["current_override_fields"]
    assert "policy.enabled" in show_payload["mutation_state"]["current_override_fields"]
    assert show_payload["effective_permissions"]["default_workspace_root"] == "profiles/analyst"
    assert show_payload["effective_permissions"]["shell_default_cwd"] == "profiles/analyst"
    assert show_payload["effective_permissions"]["file_scope_mode"] == "profile_only"
    assert show_payload["effective_permissions"]["tool_access"]["files"] == "none"
    assert show_payload["effective_permissions"]["memory_behavior"]["capability"] == "disabled"
    assert show_payload["effective_permissions"]["memory_behavior"]["explicit_cross_chat_access"] == "disabled"
    assert show_payload["linked_channels"] == []

    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert [item["id"] for item in list_payload["profiles"]] == ["analyst"]


def test_profile_add_can_enable_chat_secret_guard(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Profile CLI should persist explicit chat secret guard enablement."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    add_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "--yes",
            "--id",
            "guarded",
            "--name",
            "Guarded",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
            "--chat-secret-guard-enabled",
        ],
    )
    show_result = runner.invoke(app, ["profile", "show", "guarded", "--json"])

    assert add_result.exit_code == 0
    add_payload = json.loads(add_result.stdout)
    assert add_payload["profile"]["effective_runtime"]["chat_secret_guard_enabled"] is True

    assert show_result.exit_code == 0
    show_payload = json.loads(show_result.stdout)
    assert show_payload["profile"]["effective_runtime"]["chat_secret_guard_enabled"] is True
    assert show_payload["profile"]["runtime_config"]["chat_secret_guard_enabled"] is True


def test_profile_show_includes_linked_channels(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Profile inspection should include linked channel summaries and narrowing details."""

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
            "--policy-preset",
            "strict",
            "--policy-capability",
            "files",
            "--policy-capability",
            "memory",
            "--policy-file-access-mode",
            "read_write",
        ],
    )
    assert add_result.exit_code == 0

    settings = get_settings()
    asyncio.run(
        get_channel_endpoint_service(settings).create(
            TelegramPollingEndpointConfig(
                endpoint_id="support-bot",
                profile_id="default",
                credential_profile_key="bot-main",
                account_id="support-bot",
                group_trigger_mode="mention_or_reply",
                tool_profile="support_readonly",
            )
        )
    )

    # Act
    show_result = runner.invoke(app, ["profile", "show", "default", "--json"])

    # Assert
    assert show_result.exit_code == 0
    payload = json.loads(show_result.stdout)
    assert payload["linked_channels"] == [
        {
            "endpoint_id": "support-bot",
            "transport": "telegram",
            "adapter_kind": "telegram_bot_polling",
            "account_id": "support-bot",
            "enabled": True,
            "mode": "mention_or_reply",
        }
    ]
    assert len(payload["linked_channel_inspections"]) == 1
    inspection = payload["linked_channel_inspections"][0]
    assert inspection["channel"]["endpoint_id"] == "support-bot"
    assert inspection["channel_guardrails"]["channel_tool_profile"] == "support_readonly"
    assert "file.read" in inspection["channel_guardrails"]["channel_tool_profile_allowlist"]
    assert inspection["effective_permissions"]["default_workspace_root"] == "profiles/default"
    assert inspection["effective_permissions"]["tool_access"]["files"] == "read_only"
    assert inspection["effective_permissions"]["tool_access"]["shell"] == "disabled"
    assert inspection["effective_permissions"]["tool_access"]["memory"] == "enabled"
    assert inspection["effective_permissions"]["tool_access"]["credentials"] == "blocked_in_user_channel"


def test_profile_help_exposes_update_and_hides_runtime_group(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile help should advertise update and not expose the removed runtime subgroup."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["profile", "--help"])

    # Assert
    assert result.exit_code == 0
    assert "update" in result.stdout
    assert re.search(r"^\s*runtime\s", result.stdout, re.MULTILINE) is None


def test_profile_show_and_list_default_to_human_output(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Profile inspection defaults to human-readable output while keeping JSON opt-in."""

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
            "--policy-preset",
            "strict",
            "--policy-capability",
            "files",
            "--policy-capability",
            "memory",
            "--policy-file-access-mode",
            "read_write",
        ],
    )
    assert add_result.exit_code == 0

    # Act
    list_result = runner.invoke(app, ["profile", "list"])
    show_result = runner.invoke(app, ["profile", "show", "default"])

    # Assert
    assert list_result.exit_code == 0
    assert "- default: name=Default, provider=openai, model=gpt-4o-mini" in list_result.stdout

    assert show_result.exit_code == 0
    assert "Profile `default`" in show_result.stdout
    assert "- merge_order: explicit > current > inherited > system" in show_result.stdout
    assert "- inherited_defaults_source: global runtime settings and setup defaults" in show_result.stdout
    assert "- linked_channels: none" in show_result.stdout


def test_profile_show_human_output_includes_channel_narrowing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Human profile inspection should show per-channel effective narrowing details."""

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
            "--policy-preset",
            "strict",
            "--policy-capability",
            "files",
            "--policy-capability",
            "memory",
            "--policy-file-access-mode",
            "read_write",
        ],
    )
    assert add_result.exit_code == 0

    settings = get_settings()
    asyncio.run(
        get_channel_endpoint_service(settings).create(
            TelegramPollingEndpointConfig(
                endpoint_id="support-bot",
                profile_id="default",
                credential_profile_key="bot-main",
                account_id="support-bot",
                group_trigger_mode="mention_or_reply",
                tool_profile="support_readonly",
            )
        )
    )

    # Act
    show_result = runner.invoke(app, ["profile", "show", "default"])

    # Assert
    assert show_result.exit_code == 0
    assert "tool_profile=support_readonly" in show_result.stdout
    assert "effective_tools=files=read_only,shell=disabled,memory=enabled" in show_result.stdout


def test_profile_delete_removes_profile_and_folder(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Profile CLI should delete one non-default profile and its workspace tree."""

    # Arrange
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    add_result = runner.invoke(
        app,
        [
            "profile",
            "add",
            "support",
            "--yes",
            "--name",
            "Support",
            "--llm-provider",
            "openai",
            "--chat-model",
            "gpt-4o-mini",
        ],
    )
    assert add_result.exit_code == 0
    assert (tmp_path / "profiles/support").exists() is True

    # Act
    delete_result = runner.invoke(app, ["profile", "delete", "support", "--yes"])

    # Assert
    assert delete_result.exit_code == 0
    assert "Profile `support` deleted." in delete_result.stdout
    assert (tmp_path / "profiles/support").exists() is False
