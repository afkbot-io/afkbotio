"""Cross-family channel CLI contracts and shared mutation behavior."""


import asyncio
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channel_routing import ChannelBindingRule, get_channel_binding_service
from afkbot.services.channels.endpoint_contracts import (
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
)
from afkbot.services.channels.endpoint_service import (
    get_channel_endpoint_service,
    reset_channel_endpoint_services_async,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_mutations_request_managed_runtime_reload(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Channel mutations should ask the managed runtime to reload."""

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
    add_calls: list[str] = []
    toggle_calls: list[str] = []
    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telegram.reload_install_managed_runtime_notice",
        lambda settings: add_calls.append(str(settings.root_dir)),
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telegram_runtime.reload_install_managed_runtime_notice",
        lambda settings: toggle_calls.append(str(settings.root_dir)),
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
            "--no-binding",
        ],
    )
    assert add_result.exit_code == 0

    disable_result = runner.invoke(app, ["channel", "telegram", "disable", "support-bot"])

    assert disable_result.exit_code == 0
    assert add_calls == [str(tmp_path)]
    assert toggle_calls == [str(tmp_path)]

def test_channel_update_rejects_unknown_profile_for_both_transports(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Channel update should fail closed when switching to a profile that does not exist."""

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
            "--account-id",
            "bot-main",
            "--no-binding",
            "--yes",
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
            "--account-id",
            "personal-user",
            "--no-binding",
            "--yes",
        ],
    ).exit_code == 0

    telegram_update = runner.invoke(
        app,
        ["channel", "telegram", "update", "support-bot", "--profile", "missing-profile", "--yes"],
    )
    telethon_update = runner.invoke(
        app,
        ["channel", "telethon", "update", "personal-user", "--profile", "missing-profile", "--yes"],
    )

    assert telegram_update.exit_code == 2
    assert "Profile not found: missing-profile" in (telegram_update.stdout + telegram_update.stderr)
    assert telethon_update.exit_code == 2
    assert "Profile not found: missing-profile" in (telethon_update.stdout + telethon_update.stderr)

def test_channel_update_binding_sync_preserves_existing_binding_metadata(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Binding sync on update should preserve existing session policy, priority, and prompt overlay."""

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
            "--account-id",
            "support-bot",
            "--binding",
            "--session-policy",
            "per-user-in-group",
            "--priority",
            "7",
            "--prompt-overlay",
            "keep telegram overlay",
            "--yes",
        ],
    ).exit_code == 0
    assert runner.invoke(
        app,
        [
            "channel",
            "telegram",
            "update",
            "support-bot",
            "--binding",
            "--group-trigger-mode",
            "mention_only",
            "--yes",
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
            "--account-id",
            "personal-user",
            "--binding",
            "--session-policy",
            "per-user-in-group",
            "--priority",
            "9",
            "--prompt-overlay",
            "keep telethon overlay",
            "--yes",
        ],
    ).exit_code == 0
    assert runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--binding",
            "--reply-mode",
            "same_chat",
            "--yes",
        ],
    ).exit_code == 0

    binding_service = get_channel_binding_service(settings)
    telegram_binding = asyncio.run(binding_service.get(binding_id="support-bot"))
    telethon_binding = asyncio.run(binding_service.get(binding_id="personal-user"))

    assert telegram_binding == ChannelBindingRule(
        binding_id="support-bot",
        transport="telegram",
        profile_id="default",
        session_policy="per-user-in-group",
        priority=7,
        enabled=True,
        account_id="support-bot",
        prompt_overlay="keep telegram overlay",
    )
    assert telethon_binding == ChannelBindingRule(
        binding_id="personal-user",
        transport="telegram_user",
        profile_id="default",
        session_policy="per-user-in-group",
        priority=9,
        enabled=True,
        account_id="personal-user",
        prompt_overlay="keep telethon overlay",
    )

def test_channel_telethon_enable_rejects_non_telethon_endpoint(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Telethon enable/disable path should reject endpoints from another channel family."""

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
    asyncio.run(
        get_channel_endpoint_service(settings).create(
            TelegramPollingEndpointConfig(
                endpoint_id="support-bot",
                profile_id="default",
                credential_profile_key="bot-main",
                account_id="telegram-bot",
            )
        )
    )
    asyncio.run(reset_channel_endpoint_services_async())

    result = runner.invoke(app, ["channel", "telethon", "enable", "support-bot"])

    assert result.exit_code == 2
    assert "channel_endpoint_type_mismatch" in result.stderr

    status_result = runner.invoke(app, ["channel", "telethon", "status", "support-bot"])
    assert status_result.exit_code == 2
    assert "channel_endpoint_type_mismatch" in status_result.stderr

def test_channel_telegram_enable_rejects_non_telegram_endpoint(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Telegram enable/disable path should reject endpoints from another channel family."""

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
    asyncio.run(
        get_channel_endpoint_service(settings).create(
            TelethonUserEndpointConfig(
                endpoint_id="personal-user",
                profile_id="default",
                credential_profile_key="tg-user",
                account_id="personal-user",
            )
        )
    )
    asyncio.run(reset_channel_endpoint_services_async())

    result = runner.invoke(app, ["channel", "telegram", "enable", "personal-user"])

    assert result.exit_code == 2
    assert "channel_endpoint_type_mismatch" in result.stderr

def test_channel_telegram_show_and_delete_reject_non_telegram_endpoint(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telegram operator path should reject Telethon endpoints instead of mutating the wrong adapter family."""

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
    asyncio.run(
        get_channel_endpoint_service(settings).create(
            TelethonUserEndpointConfig(
                endpoint_id="personal-user",
                profile_id="default",
                credential_profile_key="tg-user",
                account_id="personal-user",
            )
        )
    )

    show_result = runner.invoke(app, ["channel", "telegram", "show", "personal-user"])
    assert show_result.exit_code == 2
    assert "channel_endpoint_type_mismatch" in show_result.stderr

    status_result = runner.invoke(app, ["channel", "telegram", "status", "personal-user"])
    assert status_result.exit_code == 2
    assert "channel_endpoint_type_mismatch" in status_result.stderr

    delete_result = runner.invoke(app, ["channel", "telegram", "delete", "personal-user"])
    assert delete_result.exit_code == 2
    assert "channel_endpoint_type_mismatch" in delete_result.stderr

    endpoint = asyncio.run(get_channel_endpoint_service(settings).get(endpoint_id="personal-user"))
    assert endpoint.endpoint_id == "personal-user"
