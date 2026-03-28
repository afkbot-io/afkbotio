"""Root and help-surface tests for channel CLI commands."""


import asyncio
import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channels.endpoint_contracts import (
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
)
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli._rendering import invoke_plain_help
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_root_show_includes_effective_memory_behavior(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Root channel show should expose effective memory behavior and channel guardrails."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
                memory_auto_search_enabled=True,
                memory_auto_search_scope_mode="auto",
                memory_auto_search_chat_limit=4,
                memory_auto_search_global_limit=2,
                memory_auto_save_enabled=True,
                memory_auto_save_scope_mode="thread",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("memory",),
            policy_network_allowlist=("*",),
        )
    )
    asyncio.run(
        get_channel_endpoint_service(settings).create(
            TelethonUserEndpointConfig(
                endpoint_id="personal-user",
                profile_id="default",
                credential_profile_key="tg-user",
                account_id="personal-user",
                tool_profile="messaging_safe",
            )
        )
    )

    result = runner.invoke(app, ["channel", "show", "personal-user", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mutation_state"]["merge_order"] == [
        "explicit_cli_overrides",
        "persisted_current_values",
        "inherited_defaults",
        "system_defaults",
    ]
    assert payload["mutation_state"]["inherited_defaults_source"] == "profile:default"
    assert payload["mutation_state"]["narrowing_behavior"] == "channel overlay may narrow profile permissions only"
    assert payload["profile_ceiling"]["tool_access"]["memory"] == "enabled"
    assert payload["effective_permissions"]["memory_behavior"]["auto_search_enabled"] is True
    assert payload["effective_permissions"]["memory_behavior"]["auto_save_scope_mode"] == "thread"
    assert payload["effective_permissions"]["memory_behavior"]["explicit_cross_chat_access"] == "trusted_only"

def test_channel_root_show_human_output_includes_profile_ceiling_and_merge_model(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Root channel show should explain merge order, current overrides, and profile ceiling."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
                memory_auto_search_enabled=True,
                memory_auto_search_scope_mode="chat",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files", "memory"),
            policy_file_access_mode="read_only",
            policy_network_allowlist=("api.telegram.org",),
        )
    )
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

    result = runner.invoke(app, ["channel", "show", "support-bot"])

    assert result.exit_code == 0
    assert "- merge_order: explicit > current > inherited > system" in result.stdout
    assert "- inherited_defaults_source: profile:default" in result.stdout
    assert "- current_channel_overrides: tool_profile" in result.stdout
    assert "- profile_ceiling_tool_access: files=read_only, shell=disabled, memory=enabled, credentials=disabled, apps=disabled" in result.stdout

def test_channel_telethon_show_includes_merge_model_and_profile_ceiling(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telethon family show should expose the same mutation and profile-ceiling summary."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
                memory_auto_search_enabled=True,
                memory_auto_search_scope_mode="chat",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files", "memory"),
            policy_file_access_mode="read_only",
            policy_network_allowlist=("*",),
        )
    )
    asyncio.run(
        get_channel_endpoint_service(settings).create(
            TelethonUserEndpointConfig(
                endpoint_id="personal-user",
                profile_id="default",
                credential_profile_key="tg-user",
                account_id="personal-user",
                reply_mode="same_chat",
                tool_profile="messaging_safe",
            )
        )
    )

    result = runner.invoke(app, ["channel", "telethon", "show", "personal-user"])

    assert result.exit_code == 0
    assert "- merge_order: explicit > current > inherited > system" in result.stdout
    assert "- inherited_defaults_source: profile:default" in result.stdout
    assert "- current_channel_overrides: reply_mode, tool_profile" in result.stdout
    assert "- profile_ceiling_tool_access: files=read_only, shell=disabled, memory=enabled, credentials=disabled, apps=disabled" in result.stdout

def test_channel_root_show_reports_missing_channel_cleanly(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Root channel show should emit a deterministic CLI error for missing endpoints."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["channel", "show", "missing-channel"])

    assert result.exit_code == 2
    combined = result.stdout + result.stderr
    assert "missing-channel" in combined
    assert "ERROR [" in combined

def test_channel_root_show_includes_effective_permissions(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Root channel show should expose profile-derived effective permission summary and guardrails."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="default",
            name="Default",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files", "shell"),
            policy_file_access_mode="read_only",
            policy_network_allowlist=("api.telegram.org",),
        )
    )
    endpoint_service = get_channel_endpoint_service(settings)
    asyncio.run(
        endpoint_service.create(
            TelethonUserEndpointConfig(
                endpoint_id="personal-user",
                profile_id="default",
                credential_profile_key="tg-user",
                account_id="personal-user",
                enabled=True,
                reply_mode="same_chat",
                tool_profile="support_readonly",
            )
        )
    )

    result = runner.invoke(app, ["channel", "show", "personal-user", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["profile"] == {"id": "default", "name": "Default"}
    assert payload["effective_permissions"]["default_workspace_root"] == "profiles/default"
    assert payload["effective_permissions"]["file_scope_mode"] == "profile_only"
    assert payload["effective_permissions"]["file_access_mode"] == "read_only"
    assert payload["effective_permissions"]["tool_access"]["shell"] == "disabled"
    assert payload["effective_permissions"]["tool_access"]["memory"] == "disabled"
    assert payload["effective_permissions"]["tool_access"]["files"] == "read_only"
    assert payload["effective_permissions"]["tool_access"]["credentials"] == "blocked_in_user_channel"
    assert payload["channel_guardrails"]["user_facing_transport"] is True
    assert payload["channel_guardrails"]["channel_tool_profile"] == "support_readonly"
    assert "file.read" in payload["channel_guardrails"]["channel_tool_profile_allowlist"]
    assert "credentials.list" in payload["channel_guardrails"]["hard_blocked_tools"]

    disable_result = runner.invoke(app, ["channel", "telethon", "disable", "personal-user"])
    assert disable_result.exit_code == 0
    assert "enabled=False" in disable_result.stdout

def test_channel_add_help_surfaces_telegram_and_telethon_options(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """`add --help` should expose key operator-facing options for both channel families."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    telegram_help, telegram_output = invoke_plain_help(runner, app, ["channel", "telegram", "add"])
    assert telegram_help.exit_code == 0
    assert "Group/supergroup" in telegram_output
    assert "Delay and" in telegram_output
    assert "Quiet-window" in telegram_output
    assert "Minimum seconds" in telegram_output
    assert "coalesced" in telegram_output
    assert "--tool-profile" in telegram_output
    assert "typing actions" in telegram_output
    assert "human-like" in telegram_output
    assert "Binding session" in telegram_output
    assert "Optional routing" in telegram_output

    telethon_help, telethon_output = invoke_plain_help(runner, app, ["channel", "telethon", "add"])
    assert telethon_help.exit_code == 0
    assert "Delay and" in telethon_output
    assert "Minimum seconds" in telethon_output
    assert "Maximum buffered" in telethon_output
    assert "--tool-profile" in telethon_output
    assert "read" in telethon_output
    assert "receipt" in telethon_output
    assert "Collect watched" in telethon_output
    assert "Saved Messages" in telethon_output
    assert "in-memory" in telethon_output
    assert "--no-binding" in telethon_output
    assert "existing one." in telethon_output

def test_channel_family_help_lists_expected_commands(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Top-level family help should pin the supported operator command surface."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    root_help = runner.invoke(app, ["channel", "--help"])
    assert root_help.exit_code == 0
    for command in ("list", "show", "telegram", "telethon"):
        assert command in root_help.stdout

    telegram_help = runner.invoke(app, ["channel", "telegram", "--help"])
    assert telegram_help.exit_code == 0
    for command in ("add", "update", "list", "show", "enable", "disable", "delete", "status", "poll-once", "reset-offset"):
        assert command in telegram_help.stdout

    telethon_help = runner.invoke(app, ["channel", "telethon", "--help"])
    assert telethon_help.exit_code == 0
    for command in ("add", "update", "list", "show", "dialogs", "enable", "disable", "delete", "status", "authorize", "logout", "reset-state"):
        assert command in telethon_help.stdout

def test_channel_help_surfaces_telegram_and_telethon_options(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """CLI help should expose the full operator surface for both channel families."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    telegram_help = runner.invoke(app, ["channel", "telegram", "add", "--help"])
    assert telegram_help.exit_code == 0
    assert "Group/supergroup" in telegram_help.stdout
    assert "Binding session" in telegram_help.stdout
    assert "Optional routing" in telegram_help.stdout

    telethon_help = runner.invoke(app, ["channel", "telethon", "add", "--help"])
    assert telethon_help.exit_code == 0
    assert "Collect watched" in telethon_help.stdout
    assert "Saved Messages" in telethon_help.stdout
    assert "in-memory" in telethon_help.stdout
