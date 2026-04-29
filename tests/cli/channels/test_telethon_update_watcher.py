"""Watcher and interactive Telethon channel update tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_telethon_watcher_update_validates_scripted_values(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telethon scripted update should reject invalid watcher values before endpoint validation."""

    # Arrange
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
            policy_capabilities=("files",),
            policy_network_allowlist=("*",),
        )
    )
    created = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "add",
            "personal-user",
            "--profile",
            "default",
            "--credential-profile",
            "tg-user",
        ],
    )
    assert created.exit_code == 0

    # Act
    update_result = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--watcher-enabled",
            "--watcher-batch-interval-sec",
            "5",
        ],
    )

    # Assert
    assert update_result.exit_code == 2
    assert "Digest interval (sec) must be >= 10" in update_result.stderr


def test_channel_telethon_update_preserves_enabled_state_for_humanize_and_watcher_subflags(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Scripted Telethon sub-flags should inherit current enabled state when parent flags are omitted."""

    # Arrange
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
            policy_capabilities=("files",),
            policy_network_allowlist=("*",),
        )
    )
    created = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "add",
            "personal-user",
            "--profile",
            "default",
            "--credential-profile",
            "tg-user",
            "--humanize-replies",
            "--humanize-min-delay-ms",
            "1000",
            "--watcher-enabled",
            "--watcher-batch-interval-sec",
            "60",
        ],
    )
    assert created.exit_code == 0

    # Act
    updated = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--humanize-min-delay-ms",
            "2000",
            "--watcher-batch-interval-sec",
            "90",
        ],
    )
    shown = runner.invoke(app, ["channel", "telethon", "show", "personal-user"]).stdout

    # Assert
    assert updated.exit_code == 0
    assert "- reply_humanization.enabled: True" in shown
    assert "- reply_humanization.min_delay_ms: 2000" in shown
    assert "- watcher.enabled: True" in shown
    assert "- watcher.batch_interval_sec: 90" in shown


def test_channel_telethon_update_interactive_uses_current_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telethon update should prefill current non-credential values."""

    # Arrange
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
            policy_capabilities=("files",),
            policy_network_allowlist=("*",),
        )
    )
    created = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "add",
            "personal-user",
            "--profile",
            "default",
            "--credential-profile",
            "tg-user",
            "--reply-mode",
            "same_chat",
            "--tool-profile",
            "support_readonly",
            "--group-invocation-mode",
            "reply_only",
            "--process-self-commands",
            "--command-prefix",
            ".me",
        ],
    )
    assert created.exit_code == 0

    # Act
    updated = runner.invoke(
        app,
        ["channel", "telethon", "update", "personal-user"],
        input="\n\n\n\n\n\n\n\n\n",
    )
    shown = runner.invoke(app, ["channel", "telethon", "show", "personal-user"]).stdout

    # Assert
    assert updated.exit_code == 0
    assert "- reply_mode: same_chat" in shown
    assert "- tool_profile: support_readonly" in shown
    assert "- group_invocation_mode: reply_only" in shown
    assert "- process_self_commands: True" in shown
    assert "- command_prefix: .me" in shown
    assert "- ingress_batch.enabled: False" in shown
    assert "- reply_humanization.enabled: False" in shown
    assert "- watcher.enabled: False" in shown
