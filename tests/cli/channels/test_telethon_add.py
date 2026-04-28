"""Telethon channel add-command tests."""


import asyncio
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channel_routing.service import reset_channel_binding_services_async
from afkbot.services.channel_routing.service import run_channel_binding_service_sync
from afkbot.services.channels.endpoint_service import (
    get_channel_endpoint_service,
    reset_channel_endpoint_services_async,
)
from afkbot.services.health import (
    DoctorChannelsReport,
    TelethonUserEndpointReport,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import reset_profile_services_async
from afkbot.services.setup.runtime_store import write_runtime_config
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_telethon_add_show_and_status(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Telethon operator commands should persist config and expose status."""

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

    result = runner.invoke(
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
            "--reply-allowed-chat-patterns",
            "andrey,core team",
            "--reply-blocked-chat-patterns",
            "spam room",
            "--group-invocation-mode",
            "reply_only",
            "--process-self-commands",
            "--command-prefix",
            ".me",
            "--ingress-batch-enabled",
            "--ingress-debounce-ms",
            "2500",
            "--ingress-cooldown-sec",
            "9",
            "--ingress-max-batch-size",
            "8",
            "--ingress-max-buffer-chars",
            "16000",
            "--humanize-replies",
            "--humanize-min-delay-ms",
            "1100",
            "--humanize-max-delay-ms",
            "9000",
            "--humanize-chars-per-second",
            "10",
            "--mark-read-before-reply",
            "--watcher-enabled",
            "--watcher-batch-interval-sec",
            "60",
            "--watcher-dialog-refresh-interval-sec",
            "120",
            "--watcher-no-private",
            "--watcher-include-groups",
            "--watcher-include-channels",
            "--watcher-blocked-chat-patterns",
            "spam,ads",
            "--no-binding",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "telethon", "show", "personal-user"])
    assert shown.exit_code == 0
    assert "- reply_mode: same_chat" in shown.stdout
    assert "- tool_profile: support_readonly" in shown.stdout
    assert "- reply.allowed_chat_patterns: andrey, core team" in shown.stdout
    assert "- reply.blocked_chat_patterns: spam room" in shown.stdout
    assert "- group_invocation_mode: reply_only" in shown.stdout
    assert "- process_self_commands: True" in shown.stdout
    assert "- command_prefix: .me" in shown.stdout
    assert "- ingress_batch.enabled: True" in shown.stdout
    assert "- ingress_batch.debounce_ms: 2500" in shown.stdout
    assert "- ingress_batch.cooldown_sec: 9" in shown.stdout
    assert "- ingress_batch.max_batch_size: 8" in shown.stdout
    assert "- ingress_batch.max_buffer_chars: 16000" in shown.stdout
    assert "- effective_memory_auto_search: off" in shown.stdout
    assert "- effective_memory_cross_chat_access: disabled" in shown.stdout
    assert "- reply_humanization.enabled: True" in shown.stdout
    assert "- reply_humanization.min_delay_ms: 1100" in shown.stdout
    assert "- reply_humanization.max_delay_ms: 9000" in shown.stdout
    assert "- reply_humanization.chars_per_second: 10" in shown.stdout
    assert "- mark_read_before_reply: True" in shown.stdout
    assert "- watcher.enabled: True" in shown.stdout
    assert "- watcher.include_private: False" in shown.stdout
    assert "- watcher.batch_interval_sec: 60" in shown.stdout
    assert "- watcher.dialog_refresh_interval_sec: 120" in shown.stdout
    assert "- watcher.blocked_chat_patterns: spam, ads" in shown.stdout
    assert '"transport": "telegram_user"' in shown.stdout
    assert '"peer_id": "me"' in shown.stdout

    endpoint_service = get_channel_endpoint_service(settings)
    state_path = endpoint_service.telethon_user_state_path(endpoint_id="personal-user")

    async def _fake_status(_settings: object) -> DoctorChannelsReport:
        return DoctorChannelsReport(
            telegram_polling=(),
            telethon_userbot=(
                TelethonUserEndpointReport(
                    endpoint_id="personal-user",
                    enabled=True,
                    profile_id="default",
                    credential_profile_key="tg-user",
                    account_id="personal-user",
                    profile_valid=True,
                    profile_exists=True,
                    api_id_configured=True,
                    api_hash_configured=True,
                    phone_configured=True,
                    session_string_configured=True,
                    policy_allows_runtime=True,
                    binding_count=0,
                    state_path=str(state_path),
                    state_present=False,
                ),
            ),
        )

    async def _fake_probe(*, settings: object, endpoint: object) -> object:
        _ = settings, endpoint

        class _Identity:
            user_id = 1001
            username = "me"
            phone = "+79990000000"
            display_name = "Me User"

        return _Identity()

    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telethon_runtime.run_channel_health_diagnostics",
        _fake_status,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telethon_runtime.probe_telethon_endpoint",
        _fake_probe,
    )

    status = runner.invoke(app, ["channel", "telethon", "status", "--probe"])
    assert status.exit_code == 0
    assert "Telethon userbot endpoints: 1" in status.stdout
    assert "personal-user" in status.stdout
    assert "Live probe:" in status.stdout


def test_channel_telethon_add_creates_allowlist_bindings_from_access_flags(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Telethon add should support the same scoped access controls as bot channels."""

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
            policy_capabilities=("memory",),
            policy_network_allowlist=("*",),
        )
    )

    result = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "add",
            "owner-user",
            "--profile",
            "default",
            "--credential-profile",
            "tg-user",
            "--private-policy",
            "allowlist",
            "--allow-from",
            "12345",
            "--group-policy",
            "allowlist",
            "--groups",
            "-100123",
            "--group-allow-from",
            "12345",
            "--binding",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "telethon", "show", "owner-user"]).stdout
    assert "- access.private_policy: allowlist" in shown
    assert "- access.allow_from: 12345" in shown
    assert "- access.group_policy: allowlist" in shown
    assert "- access.groups: -100123" in shown
    assert "- access.group_allow_from: 12345" in shown

    bindings = run_channel_binding_service_sync(
        settings,
        lambda service: service.list(transport="telegram_user", profile_id="default"),
    )
    binding_payloads = sorted(
        (
            item.binding_id,
            item.account_id,
            item.peer_id,
            item.user_id,
        )
        for item in bindings
    )
    assert binding_payloads == [
        ("owner-user:dm:12345", "owner-user", "12345", "12345"),
        ("owner-user:group:-100123:user:12345", "owner-user", "-100123", "12345"),
    ]

def test_channel_telethon_add_warns_when_no_binding_keeps_existing_binding(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`--no-binding` should warn when a matching binding already exists."""

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

    initial = runner.invoke(
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
            "--binding",
        ],
    )
    assert initial.exit_code == 0

    updated = runner.invoke(
        app,
        [
            "channel",
            "telethon",
            "update",
            "personal-user",
            "--watcher-enabled",
        ],
    )

    assert updated.exit_code == 0
    asyncio.run(reset_channel_endpoint_services_async())
    asyncio.run(reset_channel_binding_services_async())
    asyncio.run(reset_profile_services_async())

def test_channel_telethon_add_interactive_uses_profile_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telethon add should prefill safe defaults from the chosen profile."""

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
            policy_capabilities=("memory",),
            policy_network_allowlist=("*",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telethon", "add", "personal-user"],
        input="12345678\nhash-value\n+79990000000\n",
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "telethon", "show", "personal-user"]).stdout
    assert "- profile: default" in shown
    assert "- credential_profile: personal-user" in shown
    assert "- account_id: personal-user" in shown
    assert "- tool_profile: messaging_safe" in shown
    assert "- reply_mode: disabled" in shown
    assert "- process_self_commands: False" in shown
    assert "- ingress_batch.enabled: False" in shown
    assert "- reply_humanization.enabled: False" in shown
    assert "- watcher.enabled: False" in shown

def test_channel_telethon_add_with_profile_flag_stays_interactive(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Providing --profile should not disable the rest of interactive Telethon add prompts."""

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
            policy_capabilities=("memory",),
            policy_network_allowlist=("*",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telethon", "add", "personal-user", "--profile", "default"],
        input="12345678\nhash-value\n+79990000000\n",
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "telethon", "show", "personal-user"]).stdout
    assert "- credential_profile: personal-user" in shown
    assert "- account_id: personal-user" in shown


def test_channel_telethon_add_interactive_accepts_generated_channel_id(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telethon add should allow blank channel id and create a generated one."""

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
            policy_capabilities=("memory",),
            policy_network_allowlist=("*",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telethon", "add"],
        input="\n12345678\nhash-value\n+79990000000\n",
    )

    assert result.exit_code == 0
    assert "Telethon user channel setup" in result.stdout
    assert "Press Enter there to accept `telethon-" in result.stdout
    channels = asyncio.run(get_channel_endpoint_service(settings).list(transport="telegram_user"))
    assert len(channels) == 1
    saved = channels[0]
    assert saved.endpoint_id.startswith("telethon-")
    assert saved.credential_profile_key == saved.endpoint_id
    assert saved.account_id == saved.endpoint_id


def test_channel_telethon_add_uses_russian_locale_for_intro(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telethon add should switch to Russian when the system locale is Russian."""

    _prepare_env(tmp_path, monkeypatch)
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "ru_RU.UTF-8")
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
            policy_capabilities=("memory",),
            policy_network_allowlist=("*",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telethon", "add"],
        input="\n12345678\nhash-value\n+79990000000\n",
    )

    assert result.exit_code == 0
    assert "Настройка Telethon user-канала" in result.stdout
    assert "Нажмите Enter, чтобы принять `telethon-" in result.stdout


def test_channel_telethon_add_prefers_project_prompt_language_over_system_locale(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive Telethon add should prefer persisted project prompt language over system locale."""

    _prepare_env(tmp_path, monkeypatch)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    runner = CliRunner()
    settings = get_settings()
    write_runtime_config(settings, config={"prompt_language": "ru"})
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
            policy_capabilities=("memory",),
            policy_network_allowlist=("*",),
        )
    )

    result = runner.invoke(
        app,
        ["channel", "telethon", "add"],
        input="\n12345678\nhash-value\n+79990000000\n",
    )

    assert result.exit_code == 0
    assert "Настройка Telethon user-канала" in result.stdout
