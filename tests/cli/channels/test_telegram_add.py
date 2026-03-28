"""Telegram channel add-command tests."""


import asyncio
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_telegram_add_accepts_group_trigger_mode(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Telegram channel add should persist group trigger mode, tool profile, and batching config."""

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
            policy_network_allowlist=("api.telegram.org",),
        )
    )

    result = runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "add",
            "support-bot",
            "--profile",
            "default",
            "--credential-profile",
            "bot-main",
            "--group-trigger-mode",
            "all_messages",
            "--tool-profile",
            "support_readonly",
            "--ingress-batch-enabled",
            "--ingress-debounce-ms",
            "2400",
            "--ingress-cooldown-sec",
            "12",
            "--ingress-max-batch-size",
            "6",
            "--ingress-max-buffer-chars",
            "14000",
            "--humanize-replies",
            "--humanize-min-delay-ms",
            "900",
            "--humanize-max-delay-ms",
            "7000",
            "--humanize-chars-per-second",
            "14",
            "--no-binding",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(
        app,
        ["channel", "telegram", "show", "support-bot"],
    ).stdout
    assert "- merge_order: explicit > current > inherited > system" in shown
    assert "- inherited_defaults_source: profile:default" in shown
    assert "- current_channel_overrides: group_trigger_mode, ingress_batch, reply_humanization, tool_profile" in shown
    assert "- profile_ceiling_tool_access: files=read_write, shell=disabled, memory=disabled, credentials=disabled, apps=disabled" in shown
    assert "- group_trigger_mode: all_messages" in shown
    assert "- tool_profile: support_readonly" in shown
    assert "- ingress_batch.enabled: True" in shown
    assert "- ingress_batch.debounce_ms: 2400" in shown
    assert "- ingress_batch.cooldown_sec: 12" in shown
    assert "- ingress_batch.max_batch_size: 6" in shown
    assert "- ingress_batch.max_buffer_chars: 14000" in shown
    assert "- effective_memory_auto_search: off" in shown
    assert "- effective_memory_cross_chat_access: disabled" in shown
    assert "- reply_humanization.enabled: True" in shown
    assert "- reply_humanization.min_delay_ms: 900" in shown
    assert "- reply_humanization.max_delay_ms: 7000" in shown
    assert "- reply_humanization.chars_per_second: 14" in shown

def test_channel_telegram_add_interactive_uses_profile_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telegram add should prefill safe defaults from the chosen profile."""

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
            policy_capabilities=("files", "memory"),
            policy_file_access_mode="read_only",
            policy_network_allowlist=("api.telegram.org",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telegram", "add", "support-bot"],
        input="123456:TEST_TOKEN\n\n",
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- profile: default" in shown
    assert "- credential_profile: support-bot" in shown
    assert "- account_id: support-bot" in shown
    assert "- tool_profile: support_readonly" in shown
    assert "- ingress_batch.enabled: False" in shown
    assert "- reply_humanization.enabled: False" in shown

def test_channel_telegram_add_interactive_falls_back_to_chat_minimal_without_memory(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telegram add should default to chat_minimal when the profile has no memory."""

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
            policy_file_access_mode="read_only",
            policy_network_allowlist=("api.telegram.org",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telegram", "add", "support-bot"],
        input="123456:TEST_TOKEN\n\n",
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- tool_profile: chat_minimal" in shown

def test_channel_telegram_add_with_profile_flag_stays_interactive(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Providing --profile should not disable the rest of interactive Telegram add prompts."""

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
            policy_capabilities=("files", "memory"),
            policy_file_access_mode="read_only",
            policy_network_allowlist=("api.telegram.org",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telegram", "add", "support-bot", "--profile", "default"],
        input="123456:TEST_TOKEN\n\n",
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- credential_profile: support-bot" in shown
    assert "- account_id: support-bot" in shown

def test_channel_telegram_add_without_positional_id_prompts_for_channel_id(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telegram add should ask for channel id when it is not passed positionally."""

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
            policy_capabilities=("files", "memory"),
            policy_file_access_mode="read_only",
            policy_network_allowlist=("api.telegram.org",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telegram", "add"],
        input="support-bot\n123456:TEST_TOKEN\n\n",
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- profile: default" in shown
    assert "- credential_profile: support-bot" in shown

def test_channel_telegram_add_rejects_ingress_values_below_contract_bounds(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram channel add should reject ingress values before Pydantic validation."""

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
            policy_network_allowlist=("api.telegram.org",),
        )
    )

    result = runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "add",
            "support-bot",
            "--profile",
            "default",
            "--credential-profile",
            "bot-main",
            "--ingress-batch-enabled",
            "--ingress-debounce-ms",
            "50",
            "--no-binding",
        ],
    )

    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "Ingress debounce (ms) must be >= 100" in combined
