"""PartyFlow webhook channel add-command tests."""

import asyncio
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def test_channel_partyflow_add_persists_webhook_shape(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow add should persist webhook ingress mode, trigger mode, and batching config."""

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
            policy_network_allowlist=("api.partyflow.ru",),
        )
    )
    monkeypatch.setenv("AFKBOT_PUBLIC_CHAT_API_URL", "https://bot.example.com")
    get_settings.cache_clear()

    result = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-partyflow",
            "--profile",
            "default",
            "--credential-profile",
            "ops-partyflow",
            "--ingress-mode",
            "webhook",
            "--trigger-mode",
            "mention",
            "--include-context",
            "--context-size",
            "8",
            "--ingress-batch-enabled",
            "--ingress-debounce-ms",
            "2000",
            "--ingress-max-batch-size",
            "5",
            "--ingress-max-buffer-chars",
            "12000",
            "--reply-mode",
            "same_conversation",
            "--no-binding",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-partyflow"]).stdout
    assert "- ingress_mode: webhook" in shown
    assert "- trigger_mode: mention" in shown
    assert "- include_context: True" in shown
    assert "- context_size: 8" in shown
    assert "- reply_mode: same_conversation" in shown
    assert "- ingress_batch.enabled: True" in shown
    assert (
        "- webhook_url: https://bot.example.com/v1/channels/partyflow/ops-partyflow/webhook"
        in shown
    )


def test_channel_partyflow_add_persists_keyword_trigger_configuration(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow add should persist normalized keyword trigger values."""

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
            policy_network_allowlist=("api.partyflow.ru",),
        )
    )

    result = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-keywords",
            "--profile",
            "default",
            "--credential-profile",
            "ops-keywords",
            "--trigger-mode",
            "keywords",
            "--trigger-keywords",
            " Billing , urgent,URGENT ",
            "--no-binding",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-keywords"]).stdout
    assert "- trigger_mode: keywords" in shown
    assert "- trigger_keywords: billing, urgent" in shown
