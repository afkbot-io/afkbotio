"""PartyFlow webhook channel add-command tests."""

import asyncio
import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channel_routing import get_channel_binding_service
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
    assert "- access.private_policy: disabled" in shown
    assert "- access.group_policy: open" in shown
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


def test_channel_partyflow_add_persists_access_policy_and_scoped_bindings(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow add should use shared access policy bindings like other channel transports."""

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
            "ops-access",
            "--profile",
            "default",
            "--credential-profile",
            "ops-access",
            "--private-policy",
            "allowlist",
            "--allow-from",
            "user-1",
            "--group-policy",
            "allowlist",
            "--groups",
            "conv-1",
            "--group-allow-from",
            "user-2",
            "--outbound-allow-to",
            "conv-1",
            "--binding",
            "--session-policy",
            "per-thread",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-access"]).stdout
    assert "- access.private_policy: allowlist" in shown
    assert "- access.allow_from: user-1" in shown
    assert "- access.group_policy: allowlist" in shown
    assert "- access.groups: conv-1" in shown
    assert "- access.group_allow_from: user-2" in shown
    assert "- access.outbound_allow_to: conv-1" in shown
    binding_service = get_channel_binding_service(settings)
    dm_binding = asyncio.run(binding_service.get(binding_id="ops-access:dm:user-1"))
    group_binding = asyncio.run(
        binding_service.get(binding_id="ops-access:group:conv-1:user:user-2")
    )
    assert dm_binding.peer_id is None
    assert dm_binding.user_id == "user-1"
    assert group_binding.peer_id == "conv-1"
    assert group_binding.user_id == "user-2"


def test_channel_partyflow_show_marks_webhook_url_unavailable_without_public_base_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow show should not suggest a localhost webhook URL when no public base URL is configured."""

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
            "ops-no-public-url",
            "--profile",
            "default",
            "--credential-profile",
            "ops-no-public-url",
            "--no-binding",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-no-public-url"]).stdout
    assert "- webhook_url: unavailable" in shown


def test_channel_partyflow_show_rejects_non_https_public_base_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow show should not suggest plain HTTP because PartyFlow requires HTTPS."""

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
    monkeypatch.setenv("AFKBOT_PUBLIC_CHAT_API_URL", "http://localhost:8080")
    get_settings.cache_clear()

    result = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-http-url",
            "--profile",
            "default",
            "--credential-profile",
            "ops-http-url",
            "--no-binding",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-http-url"]).stdout
    assert "- webhook_url: unavailable" in shown
    assert "must use public HTTPS" in shown


def test_channel_partyflow_webhook_url_command_returns_copyable_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Dedicated webhook-url command should print only the URL when configured correctly."""

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

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-webhook-url",
            "--profile",
            "default",
            "--credential-profile",
            "ops-webhook-url",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    shown = runner.invoke(
        app,
        ["channel", "partyflow", "webhook-url", "ops-webhook-url"],
    )
    assert shown.exit_code == 0
    assert (
        shown.stdout.strip()
        == "https://bot.example.com/v1/channels/partyflow/ops-webhook-url/webhook"
    )

    status = runner.invoke(
        app,
        ["channel", "partyflow", "status", "ops-webhook-url", "--json"],
    )
    assert status.exit_code == 1
    payload = json.loads(status.stdout)
    row = payload["partyflow_webhooks"][0]
    assert row["webhook_url_status"] == "ok"
    assert row["bot_token_configured"] is False
    assert row["signing_secret_configured"] is False


def test_channel_partyflow_show_rejects_private_hostname_suffixes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow show should not treat obvious private hostname suffixes as public webhook URLs."""

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
    monkeypatch.setenv("AFKBOT_PUBLIC_CHAT_API_URL", "https://bot.internal")
    get_settings.cache_clear()

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-private-host",
            "--profile",
            "default",
            "--credential-profile",
            "ops-private-host",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-private-host"]).stdout
    assert "- webhook_url: unavailable" in shown
    assert "localhost/private" in shown
