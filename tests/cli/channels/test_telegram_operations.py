"""Telegram runtime and operational CLI tests."""


import asyncio
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channels.endpoint_contracts import (
    TelegramPollingEndpointConfig,
)
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.health import (
    DoctorChannelsReport,
    TelegramPollingEndpointReport,
)
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_telegram_status_and_reset_offset(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Telegram operator commands should expose status/probe and state reset."""

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
    endpoint_service = get_channel_endpoint_service(settings)
    asyncio.run(
        endpoint_service.create(
            TelegramPollingEndpointConfig(
                endpoint_id="support-bot",
                profile_id="default",
                credential_profile_key="bot-main",
                account_id="telegram-bot",
                enabled=True,
                group_trigger_mode="mention_or_reply",
            )
        )
    )
    state_path = endpoint_service.telegram_polling_state_path(endpoint_id="support-bot")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text('{"next_update_offset": 10}', encoding="utf-8")

    async def _fake_status(_settings: object) -> DoctorChannelsReport:
        return DoctorChannelsReport(
            telegram_polling=(
                TelegramPollingEndpointReport(
                    endpoint_id="support-bot",
                    enabled=True,
                    profile_id="default",
                    credential_profile_key="bot-main",
                    account_id="telegram-bot",
                    profile_valid=True,
                    profile_exists=True,
                    token_configured=True,
                    binding_count=1,
                    state_path=str(state_path),
                    state_present=True,
                ),
            )
        )

    class _FakeIdentity:
        bot_id = 1001
        username = "afkbot"

    async def _fake_probe(self) -> _FakeIdentity:  # type: ignore[no-untyped-def]
        return _FakeIdentity()

    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telegram_runtime.run_channel_health_diagnostics",
        _fake_status,
    )
    monkeypatch.setattr(
        "afkbot.cli.commands.channel_telegram_runtime.TelegramPollingService.probe_identity",
        _fake_probe,
    )

    status_result = runner.invoke(app, ["channel", "telegram", "status", "--probe"])
    assert status_result.exit_code == 0
    assert "Telegram polling endpoints: 1" in status_result.stdout
    assert "support-bot" in status_result.stdout
    assert "Live probe:" in status_result.stdout

    reset_result = runner.invoke(app, ["channel", "telegram", "reset-offset", "support-bot"])
    assert reset_result.exit_code == 0
    assert "removed" in reset_result.stdout
    assert state_path.exists() is False

    disable_result = runner.invoke(app, ["channel", "telegram", "disable", "support-bot"])
    assert disable_result.exit_code == 0
    assert "enabled=False" in disable_result.stdout

    endpoint = asyncio.run(endpoint_service.get(endpoint_id="support-bot"))
    assert endpoint.enabled is False

def test_channel_telegram_poll_once_requires_endpoint_id(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """`poll-once` must keep its required endpoint id argument documented and enforced."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["channel", "telegram", "poll-once"])

    assert result.exit_code != 0
    assert "Missing argument 'CHANNEL_ID'" in result.stdout or "Missing argument 'CHANNEL_ID'" in result.stderr
