"""PartyFlow polling channel add/status-command tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channel_routing import get_channel_binding_service
from afkbot.cli.commands.channel_credentials_support import _upsert_app_secret
from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def _create_default_profile(tmp_path: Path, monkeypatch: MonkeyPatch) -> CliRunner:
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
    return runner


def test_channel_partyflow_add_persists_polling_shape(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runner = _create_default_profile(tmp_path, monkeypatch)

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
            "--trigger-mode",
            "mention",
            "--ingress-batch-enabled",
            "--ingress-debounce-ms",
            "2000",
            "--ingress-max-batch-size",
            "5",
            "--reply-mode",
            "same_conversation",
            "--tool-profile",
            "messaging_safe",
            "--binding",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "PartyFlow polling channel created" in result.stdout
    assert "- configure PartyFlow bot event_delivery_mode: poll" in result.stdout

    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-partyflow"]).stdout
    assert "- delivery_mode: poll" in shown
    assert "- trigger_mode: mention" in shown
    assert "- reply_mode: same_conversation" in shown
    assert "- ingress_batch.enabled: True" in shown
    assert "- ingress_batch.debounce_ms: 2000" in shown

    binding = asyncio.run(get_channel_binding_service(get_settings()).get(binding_id="ops-partyflow"))
    assert binding.transport == "partyflow"
    assert binding.profile_id == "default"


def test_channel_partyflow_add_persists_keyword_trigger_configuration(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runner = _create_default_profile(tmp_path, monkeypatch)

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
            " Billing ,urgent,billing ",
            "--no-binding",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-keywords"]).stdout
    assert "- trigger_mode: keywords" in shown
    assert "- trigger_keywords: billing, urgent" in shown


def test_channel_partyflow_status_reports_polling_state_and_bot_token(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runner = _create_default_profile(tmp_path, monkeypatch)
    settings = get_settings()
    _upsert_app_secret(
        settings=settings,
        profile_id="default",
        app_name="partyflow",
        credential_profile_key="ops-partyflow",
        credential_name="partyflow_bot_token",
        secret_value="fri_bot_test",
    )
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
            "--no-binding",
            "--yes",
        ],
    )
    assert result.exit_code == 0

    status = runner.invoke(app, ["channel", "partyflow", "status", "ops-partyflow", "--json"])

    assert status.exit_code == 0
    payload = json.loads(status.stdout)
    row = payload["partyflow_polling"][0]
    assert row["delivery_mode"] == "poll"
    assert row["bot_token_configured"] is True
    assert row["state_present"] is False


def test_channel_partyflow_status_marks_legacy_webhook_as_unsupported(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    runner = _create_default_profile(tmp_path, monkeypatch)
    settings = get_settings()
    asyncio.run(
        get_channel_endpoint_service(settings).create(
            ChannelEndpointConfig(
                endpoint_id="legacy-partyflow",
                transport="partyflow",
                adapter_kind="partyflow_webhook",
                profile_id="default",
                credential_profile_key="legacy-partyflow",
                account_id="legacy-partyflow",
                config={"ingress_mode": "webhook"},
            )
        )
    )

    status = runner.invoke(app, ["channel", "partyflow", "status", "--json"])
    list_result = runner.invoke(app, ["channel", "partyflow", "list", "--json"])
    show = runner.invoke(app, ["channel", "partyflow", "show", "legacy-partyflow"])

    assert status.exit_code == 1
    status_payload = json.loads(status.stdout)
    assert status_payload["partyflow_polling"] == []
    assert status_payload["unsupported_partyflow"][0]["endpoint_id"] == "legacy-partyflow"
    assert status_payload["unsupported_partyflow"][0]["adapter_kind"] == "partyflow_webhook"
    assert "no longer supported" in status_payload["unsupported_partyflow"][0]["reason"]
    assert list_result.exit_code == 0
    list_payload = json.loads(list_result.stdout)
    assert list_payload["channels"] == []
    assert list_payload["unsupported_partyflow"][0]["endpoint_id"] == "legacy-partyflow"
    assert show.exit_code == 2
    assert "not a PartyFlow polling channel" in show.stderr


def test_channel_partyflow_poll_once_uses_async_endpoint_load(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """poll-once should not call sync endpoint helpers from inside asyncio.run."""

    runner = _create_default_profile(tmp_path, monkeypatch)
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
            "--trigger-mode",
            "all",
            "--reply-mode",
            "disabled",
            "--no-binding",
            "--yes",
        ],
    )
    assert result.exit_code == 0

    class _FakePartyFlowPollingService:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def poll_once(self) -> int:
            return 3

    monkeypatch.setattr(
        "afkbot.cli.commands.channel_partyflow.PartyFlowPollingService",
        _FakePartyFlowPollingService,
    )

    poll = runner.invoke(app, ["channel", "partyflow", "poll-once", "ops-partyflow", "--json"])

    assert poll.exit_code == 0
    payload = json.loads(poll.stdout)
    assert payload["ok"] is True
    assert payload["processed_events"] == 3
