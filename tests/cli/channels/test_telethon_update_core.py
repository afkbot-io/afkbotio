"""Core Telethon channel update-command tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.commands import channel_telethon
from afkbot.cli.commands.channel_telethon_commands import register_telethon_command_tree
from afkbot.cli.main import app
from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_telethon_update_rejects_ingress_values_below_contract_bounds(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telethon channel update should reject ingress values before Pydantic validation."""

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
            policy_capabilities=("files", "memory"),
            policy_network_allowlist=("*",),
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
            )
        )
    )

    # Act
    result = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--ingress-max-buffer-chars",
            "128",
        ],
    )

    # Assert
    assert result.exit_code != 0
    combined = result.stdout + result.stderr
    assert "Ingress max buffer chars must be >= 256" in combined


def test_channel_telethon_update_preserves_unspecified_fields_and_root_list_show(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telethon update should keep existing values, and root list/show should surface both families."""

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
    assert runner.invoke(
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
    ).exit_code == 0
    assert runner.invoke(
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
            "chat_minimal",
            "--group-invocation-mode",
            "reply_only",
        ],
    ).exit_code == 0

    # Act
    updated = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--tool-profile",
            "support_readonly",
            "--watcher-enabled",
            "--watcher-batch-interval-sec",
            "90",
        ],
    )
    shown = runner.invoke(app, ["channel", "telethon", "show", "personal-user"]).stdout
    listed = runner.invoke(app, ["channel", "list"])
    root_show = runner.invoke(app, ["channel", "show", "personal-user"])
    telegram_root_show = runner.invoke(app, ["channel", "show", "support-bot"])

    # Assert
    assert updated.exit_code == 0
    assert "- reply_mode: same_chat" in shown
    assert "- tool_profile: support_readonly" in shown
    assert "- group_invocation_mode: reply_only" in shown
    assert "- watcher.enabled: True" in shown
    assert "- watcher.batch_interval_sec: 90" in shown

    assert listed.exit_code == 0
    assert "support-bot" in listed.stdout
    assert "personal-user" in listed.stdout
    assert "mode=mention_or_reply" in listed.stdout
    assert "tool_profile=support_readonly" in listed.stdout
    assert "reply_mode=same_chat" in listed.stdout
    assert "watcher=True" in listed.stdout

    assert root_show.exit_code == 0
    assert "- transport: telegram_user" in root_show.stdout
    assert "- channel_tool_profile: support_readonly" in root_show.stdout
    assert "- reply_mode: same_chat" in root_show.stdout

    assert telegram_root_show.exit_code == 0
    assert "- transport: telegram" in telegram_root_show.stdout
    assert "- group_trigger_mode: mention_or_reply" in telegram_root_show.stdout


def test_channel_telethon_update_falls_back_when_current_credential_profile_is_missing(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telethon update should tolerate legacy endpoints with missing credential profile keys."""

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

    template = TelethonUserEndpointConfig(
        endpoint_id="template",
        profile_id="default",
        credential_profile_key="template",
        account_id="template",
    )
    current = TelethonUserEndpointConfig.model_construct(
        endpoint_id="personal-user",
        transport="telegram_user",
        adapter_kind="telethon_userbot",
        profile_id="default",
        credential_profile_key=None,
        account_id="personal-user",
        enabled=True,
        reply_mode="same_chat",
        tool_profile="inherit",
        reply_blocked_chat_patterns=(),
        reply_allowed_chat_patterns=(),
        group_invocation_mode="reply_or_command",
        process_self_commands=False,
        command_prefix=".afk",
        ingress_batch=template.ingress_batch,
        reply_humanization=template.reply_humanization,
        mark_read_before_reply=True,
        watcher=template.watcher,
        config={},
    )
    saved: dict[str, TelethonUserEndpointConfig] = {}

    class _FakeEndpointService:
        async def get(self, *, endpoint_id: str) -> TelethonUserEndpointConfig:
            assert endpoint_id == "personal-user"
            return current

        async def update(self, endpoint: TelethonUserEndpointConfig) -> TelethonUserEndpointConfig:
            saved["endpoint"] = endpoint
            return endpoint

    async def _load_current(*, channel_id: str) -> TelethonUserEndpointConfig:
        assert channel_id == "personal-user"
        return current

    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telethon.load_telethon_endpoint",
        _load_current,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telethon.get_channel_endpoint_service",
        lambda _settings: _FakeEndpointService(),
    )

    # Act
    result = runner.invoke(app, ["channel", "telethon", "update", "personal-user", "--yes"])

    # Assert
    assert result.exit_code == 0
    assert saved["endpoint"].credential_profile_key == "personal-user"


def test_channel_telethon_update_normalizes_scripted_choice_values(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Scripted Telethon update should normalize uppercase choice values."""

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
    updated = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--reply-mode",
            "SAME_CHAT",
            "--tool-profile",
            "SUPPORT_READONLY",
            "--group-invocation-mode",
            "REPLY_ONLY",
        ],
    )
    shown = runner.invoke(app, ["channel", "telethon", "show", "personal-user"]).stdout

    # Assert
    assert updated.exit_code == 0
    assert "- reply_mode: same_chat" in shown
    assert "- tool_profile: support_readonly" in shown
    assert "- group_invocation_mode: reply_only" in shown


def test_channel_telethon_update_normalizes_profile_override(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Scripted Telethon update should normalize profile overrides before validation and save."""

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
    updated = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--profile",
            " Default ",
        ],
    )
    shown = runner.invoke(app, ["channel", "telethon", "show", "personal-user"]).stdout

    # Assert
    assert updated.exit_code == 0
    assert "- profile: default" in shown


def test_channel_telethon_ingress_cli_validation_matches_endpoint_bounds(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telethon add/update should reject ingress values below the endpoint-contract bounds."""

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

    # Act
    add_result = runner.invoke(
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
            "--ingress-batch-enabled",
            "--ingress-debounce-ms",
            "50",
        ],
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
    update_result = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--ingress-batch-enabled",
            "--ingress-max-buffer-chars",
            "100",
        ],
    )

    # Assert
    assert add_result.exit_code == 2
    assert "Ingress debounce (ms) must be >= 100" in add_result.stderr
    assert created.exit_code == 0
    assert update_result.exit_code == 2
    assert "Ingress max buffer chars must be >= 256" in update_result.stderr


def test_channel_telethon_package_export_registers_tree() -> None:
    """Telethon package exports should expose a valid registration helper."""

    # Arrange
    runner = CliRunner()
    package_app = typer.Typer()
    facade_app = typer.Typer()

    # Act
    register_telethon_command_tree(package_app)
    channel_telethon.register_telethon_commands(facade_app)
    package_help = runner.invoke(package_app, ["telethon", "--help"])
    facade_help = runner.invoke(facade_app, ["telethon", "--help"])

    # Assert
    assert package_help.exit_code == 0
    assert facade_help.exit_code == 0
    for command in ("add", "update", "list", "show", "dialogs", "status", "authorize"):
        assert command in package_help.stdout
        assert command in facade_help.stdout
