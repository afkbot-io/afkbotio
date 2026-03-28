"""Telegram channel update-command tests."""


import asyncio
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channels.endpoint_contracts import (
    TelegramPollingEndpointConfig,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_telegram_add_rejects_existing_endpoint_and_update_preserves_unspecified_fields(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram add should fail for existing ids, while update should only change explicit fields."""

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

    created = runner.invoke(
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
            "mention_only",
            "--tool-profile",
            "chat_minimal",
        ],
    )
    assert created.exit_code == 0

    duplicate = runner.invoke(
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
        ],
    )
    assert duplicate.exit_code == 2
    assert "channel_endpoint_exists" in duplicate.stderr

    updated = runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "update",
            "support-bot",
            "--tool-profile",
            "support_readonly",
            "--ingress-batch-enabled",
            "--ingress-debounce-ms",
            "2200",
        ],
    )
    assert updated.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- group_trigger_mode: mention_only" in shown
    assert "- tool_profile: support_readonly" in shown
    assert "- ingress_batch.enabled: True" in shown
    assert "- ingress_batch.debounce_ms: 2200" in shown

def test_channel_telegram_update_interactive_uses_current_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telegram update should prefill current values when no flags are passed."""

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

    created = runner.invoke(
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
            "mention_only",
            "--tool-profile",
            "support_readonly",
        ],
    )
    assert created.exit_code == 0

    updated = runner.invoke(
        app,
        ["channel", "telegram", "update", "support-bot"],
        input="\n\n\n\n",
    )

    assert updated.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- group_trigger_mode: mention_only" in shown
    assert "- tool_profile: support_readonly" in shown
    assert "- ingress_batch.enabled: False" in shown
    assert "- reply_humanization.enabled: False" in shown

def test_channel_telegram_update_falls_back_when_current_credential_profile_is_missing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram update should tolerate legacy endpoints with missing credential profile keys."""

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

    template = TelegramPollingEndpointConfig(
        endpoint_id="template",
        profile_id="default",
        credential_profile_key="template",
        account_id="template",
    )
    current = TelegramPollingEndpointConfig.model_construct(
        endpoint_id="support-bot",
        transport="telegram",
        adapter_kind="telegram_bot_polling",
        profile_id="default",
        credential_profile_key=None,
        account_id="support-bot",
        enabled=True,
        group_trigger_mode="mention_or_reply",
        tool_profile="inherit",
        ingress_batch=template.ingress_batch,
        reply_humanization=template.reply_humanization,
        config={},
    )
    saved: dict[str, TelegramPollingEndpointConfig] = {}

    class _FakeEndpointService:
        async def get(self, *, endpoint_id: str) -> TelegramPollingEndpointConfig:
            assert endpoint_id == "support-bot"
            return current

        async def update(self, endpoint: TelegramPollingEndpointConfig) -> TelegramPollingEndpointConfig:
            saved["endpoint"] = endpoint
            return endpoint

    async def _load_current(*, channel_id: str) -> TelegramPollingEndpointConfig:
        assert channel_id == "support-bot"
        return current

    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telegram.load_telegram_endpoint",
        _load_current,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telegram.get_channel_endpoint_service",
        lambda _settings: _FakeEndpointService(),
    )

    result = runner.invoke(app, ["channel", "telegram", "update", "support-bot", "--yes"])

    assert result.exit_code == 0
    assert saved["endpoint"].credential_profile_key == "support-bot"

def test_channel_telegram_update_normalizes_scripted_choice_values(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Scripted Telegram update should normalize uppercase choice values."""

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

    created = runner.invoke(
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
        ],
    )
    assert created.exit_code == 0

    updated = runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "update",
            "support-bot",
            "--group-trigger-mode",
            "REPLY_ONLY",
            "--tool-profile",
            "SUPPORT_READONLY",
        ],
    )

    assert updated.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- group_trigger_mode: reply_only" in shown
    assert "- tool_profile: support_readonly" in shown

def test_channel_telegram_update_normalizes_profile_override(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Scripted Telegram update should normalize profile overrides before validation and save."""

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

    created = runner.invoke(
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
        ],
    )
    assert created.exit_code == 0

    updated = runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "update",
            "support-bot",
            "--profile",
            " Default ",
        ],
    )

    assert updated.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- profile: default" in shown

def test_channel_telegram_ingress_cli_validation_matches_endpoint_bounds(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram add/update should reject ingress values below the endpoint-contract bounds."""

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

    add_result = runner.invoke(
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
        ],
    )

    assert add_result.exit_code == 2
    assert "Ingress debounce (ms) must be >= 100" in add_result.stderr

    created = runner.invoke(
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
        ],
    )
    assert created.exit_code == 0

    update_result = runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "update",
            "support-bot",
            "--ingress-batch-enabled",
            "--ingress-max-buffer-chars",
            "100",
        ],
    )

    assert update_result.exit_code == 2
    assert "Ingress max buffer chars must be >= 256" in update_result.stderr

def test_channel_telegram_humanization_update_validates_scripted_values(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram scripted update should reject invalid humanization values before endpoint validation."""

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
    created = runner.invoke(
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
        ],
    )
    assert created.exit_code == 0

    update_result = runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "update",
            "support-bot",
            "--humanize-replies",
            "--humanize-chars-per-second",
            "0",
        ],
    )

    assert update_result.exit_code == 2
    assert "Typing speed (chars/sec) must be >= 1" in update_result.stderr

def test_channel_telegram_humanization_update_preserves_enabled_state_for_subflags(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Scripted Telegram humanization sub-flags should work without repeating the parent enable flag."""

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
    created = runner.invoke(
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
            "--humanize-replies",
            "--humanize-min-delay-ms",
            "1000",
        ],
    )
    assert created.exit_code == 0

    updated = runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "update",
            "support-bot",
            "--humanize-min-delay-ms",
            "2000",
        ],
    )

    assert updated.exit_code == 0
    shown = runner.invoke(app, ["channel", "telegram", "show", "support-bot"]).stdout
    assert "- reply_humanization.enabled: True" in shown
    assert "- reply_humanization.min_delay_ms: 2000" in shown
