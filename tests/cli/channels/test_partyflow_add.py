"""PartyFlow webhook channel add-command tests."""

import asyncio
import json
from pathlib import Path

import pytest
from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.services.channel_routing import get_channel_binding_service
from afkbot.services.credentials import get_credentials_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def _local_partyflow_webhook_url(channel_id: str) -> str:
    settings = get_settings()
    return (
        f"http://127.0.0.1:{settings.runtime_port + 1}/v1/channels/partyflow/{channel_id}/webhook"
    )


def _load_profile_policy_json(profile_id: str) -> dict[str, list[str]]:
    settings = get_settings()

    async def _load() -> dict[str, list[str]]:
        engine = create_engine(settings)
        session_factory = create_session_factory(engine)
        try:
            async with session_scope(session_factory) as session:
                row = await ProfilePolicyRepository(session).get(profile_id)
                assert row is not None
                return {
                    "capabilities": json.loads(row.policy_capabilities_json),
                    "allowed_tools": json.loads(row.allowed_tools_json),
                    "network_allowlist": json.loads(row.network_allowlist_json),
                }
        finally:
            await engine.dispose()

    return asyncio.run(_load())


def _set_profile_policy_json(
    profile_id: str,
    *,
    capabilities: list[str] | None = None,
    allowed_tools: list[str] | None = None,
    denied_tools: list[str] | None = None,
    network_allowlist: list[str] | None = None,
) -> None:
    settings = get_settings()

    async def _set() -> None:
        engine = create_engine(settings)
        session_factory = create_session_factory(engine)
        try:
            async with session_scope(session_factory) as session:
                row = await ProfilePolicyRepository(session).get(profile_id)
                assert row is not None
                if capabilities is not None:
                    row.policy_capabilities_json = json.dumps(capabilities, ensure_ascii=True)
                if allowed_tools is not None:
                    row.allowed_tools_json = json.dumps(allowed_tools, ensure_ascii=True)
                if denied_tools is not None:
                    row.denied_tools_json = json.dumps(denied_tools, ensure_ascii=True)
                if network_allowlist is not None:
                    row.network_allowlist_json = json.dumps(network_allowlist, ensure_ascii=True)
        finally:
            await engine.dispose()

    asyncio.run(_set())


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


def test_channel_partyflow_add_extends_profile_policy_for_runtime_start(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow setup should prevent inactive runtime caused by missing profile app policy."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="partyflow",
            name="PartyFlow",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.openai.com",),
        )
    )

    result = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-policy",
            "--profile",
            "partyflow",
            "--credential-profile",
            "ops-policy",
            "--no-binding",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "- profile_policy: updated for PartyFlow runtime" in result.stdout
    policy = _load_profile_policy_json("partyflow")
    assert "apps" in policy["capabilities"]
    assert "app.run" in policy["allowed_tools"]
    assert "app.list" not in policy["allowed_tools"]
    assert "channel.send" not in policy["allowed_tools"]
    assert "api.openai.com" in policy["network_allowlist"]
    assert "api.partyflow.ru" in policy["network_allowlist"]


def test_channel_partyflow_add_respects_existing_policy_wildcards(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow setup should not expand already-sufficient policy wildcards."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="partyflow",
            name="PartyFlow",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.openai.com",),
        )
    )
    _set_profile_policy_json(
        "partyflow",
        allowed_tools=["app.*", "channel.*"],
        network_allowlist=["partyflow.ru"],
    )

    result = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-wildcard-policy",
            "--profile",
            "partyflow",
            "--credential-profile",
            "ops-wildcard-policy",
            "--no-binding",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert "- profile_policy:" not in result.stdout
    policy = _load_profile_policy_json("partyflow")
    assert policy["capabilities"] == ["files"]
    assert policy["allowed_tools"] == ["app.*", "channel.*"]
    assert policy["network_allowlist"] == ["partyflow.ru"]


def test_channel_partyflow_add_rejects_explicit_profile_policy_denies(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow setup must not override explicit profile deny rules."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="partyflow",
            name="PartyFlow",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.openai.com",),
        )
    )
    _set_profile_policy_json("partyflow", denied_tools=["app.run"])

    result = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-denied-policy",
            "--profile",
            "partyflow",
            "--credential-profile",
            "ops-denied-policy",
            "--no-binding",
            "--yes",
        ],
    )

    assert result.exit_code == 2
    assert "ERROR [partyflow_profile_policy_denies_runtime]" in result.stderr
    assert "app.run" in result.stderr


def test_channel_partyflow_enable_extends_policy_for_existing_channels(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Enabling an old PartyFlow channel should repair runtime policy before restart."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="partyflow",
            name="PartyFlow",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.openai.com",),
        )
    )
    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-enable-policy",
            "--profile",
            "partyflow",
            "--credential-profile",
            "ops-enable-policy",
            "--disabled",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    _set_profile_policy_json(
        "partyflow",
        capabilities=["files"],
        allowed_tools=["diffs.render", "file.*"],
        network_allowlist=["api.openai.com"],
    )

    enabled = runner.invoke(app, ["channel", "partyflow", "enable", "ops-enable-policy"])

    assert enabled.exit_code == 0
    assert "- profile_policy: updated for PartyFlow runtime" in enabled.stdout
    policy = _load_profile_policy_json("partyflow")
    assert "apps" in policy["capabilities"]
    assert "app.run" in policy["allowed_tools"]
    assert "channel.send" not in policy["allowed_tools"]
    assert "api.partyflow.ru" in policy["network_allowlist"]


def test_channel_partyflow_add_json_reports_profile_policy_adjustment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Machine-readable add output should include policy changes made for runtime readiness."""

    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()
    profile_service = _new_profile_service(settings)
    asyncio.run(
        profile_service.create(
            profile_id="partyflow",
            name="PartyFlow",
            runtime_config=ProfileRuntimeConfig(
                llm_provider="openai",
                llm_model="gpt-4o-mini",
            ),
            runtime_secrets=None,
            policy_enabled=True,
            policy_preset="medium",
            policy_capabilities=("files",),
            policy_network_allowlist=("api.openai.com",),
        )
    )

    result = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-policy-json",
            "--profile",
            "partyflow",
            "--credential-profile",
            "ops-policy-json",
            "--no-binding",
            "--yes",
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["channel"]["endpoint_id"] == "ops-policy-json"
    assert payload["profile_policy_adjustment"] == {
        "changed": True,
        "added_capabilities": ["apps"],
        "added_tools": ["app.run"],
        "added_network_hosts": ["api.partyflow.ru"],
        "denied_tools": [],
    }


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


def test_channel_partyflow_show_uses_local_webhook_url_without_public_base_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow show should expose a local Chat API webhook URL when no public URL is configured."""

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
    assert f"- webhook_url: {_local_partyflow_webhook_url('ops-no-public-url')}" in shown
    assert "webhook_url uses local AFKBOT Chat API" in shown


def test_channel_partyflow_show_falls_back_to_local_for_non_https_public_base_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow show should keep a local URL visible when public URL config is not usable."""

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
    assert f"- webhook_url: {_local_partyflow_webhook_url('ops-http-url')}" in shown
    assert "must use public HTTPS" in shown
    assert "webhook_url uses local AFKBOT Chat API" in shown


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
    assert row["webhook_url_source"] == "public"
    assert row["webhook_url_reason"] is None
    assert row["webhook_url_public_delivery_ready"] is True
    assert row["bot_token_configured"] is False
    assert row["signing_secret_configured"] is False
    assert row["signature_validation"] == "disabled"
    assert "signing_secret_error" not in row


def test_channel_partyflow_webhook_url_command_returns_local_url_without_public_base(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Dedicated webhook-url command should reveal the local Chat API URL by default."""

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

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-local-webhook-url",
            "--profile",
            "default",
            "--credential-profile",
            "ops-local-webhook-url",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    shown = runner.invoke(
        app,
        ["channel", "partyflow", "webhook-url", "ops-local-webhook-url"],
    )
    assert shown.exit_code == 0
    assert shown.stdout.strip() == _local_partyflow_webhook_url("ops-local-webhook-url")

    json_result = runner.invoke(
        app,
        ["channel", "partyflow", "webhook-url", "ops-local-webhook-url", "--json"],
    )
    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["webhook_url"] == _local_partyflow_webhook_url("ops-local-webhook-url")
    assert payload["source"] == "local"
    assert payload["reason"] == "missing_public_base_url"
    assert payload["status"] == "local_only"
    assert payload["public_delivery_ready"] is False
    assert "public HTTPS" in payload["warning"]


def test_channel_partyflow_add_prints_local_webhook_url_and_public_delivery_warning(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Add output should give the local URL but warn that PartyFlow needs public HTTPS."""

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

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-add-local-url",
            "--profile",
            "default",
            "--credential-profile",
            "ops-add-local-url",
            "--trigger-mode",
            "mention",
            "--no-binding",
            "--yes",
        ],
    )

    assert created.exit_code == 0
    assert f"- webhook_url: {_local_partyflow_webhook_url('ops-add-local-url')}" in created.stdout
    assert "webhook_url uses local AFKBOT Chat API" in created.stdout
    assert "public HTTPS tunnel/reverse proxy" in created.stdout


def test_channel_partyflow_show_json_reports_local_webhook_readiness(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Show JSON should expose URL source and public delivery readiness."""

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

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-show-json-local",
            "--profile",
            "default",
            "--credential-profile",
            "ops-show-json-local",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    shown = runner.invoke(app, ["channel", "partyflow", "show", "ops-show-json-local", "--json"])
    assert shown.exit_code == 0
    payload = json.loads(shown.stdout)
    assert payload["webhook_url"] == _local_partyflow_webhook_url("ops-show-json-local")
    assert payload["webhook_url_status"] == "local_only"
    assert payload["webhook_url_source"] == "local"
    assert payload["webhook_url_reason"] == "missing_public_base_url"
    assert payload["webhook_url_public_delivery_ready"] is False


def test_channel_partyflow_webhook_url_probe_json_keeps_local_url_local_only(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """A successful credential probe must not turn a local-only URL into public-ready."""

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

    async def fake_probe(**_: object) -> dict[str, object]:
        return {"ok": True, "bot_id": "bot-1", "display_name": "AFK Bot"}

    monkeypatch.setattr(
        "afkbot.cli.commands.channel_partyflow._probe_partyflow_endpoint", fake_probe
    )

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-probe-local",
            "--profile",
            "default",
            "--credential-profile",
            "ops-probe-local",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    probed = runner.invoke(
        app,
        ["channel", "partyflow", "webhook-url", "ops-probe-local", "--probe", "--json"],
    )
    assert probed.exit_code == 0
    payload = json.loads(probed.stdout)
    assert payload["webhook_url"] == _local_partyflow_webhook_url("ops-probe-local")
    assert payload["status"] == "local_only"
    assert payload["source"] == "local"
    assert payload["public_delivery_ready"] is False
    assert "public HTTPS" in payload["warning"]


def test_channel_partyflow_status_marks_local_webhook_url_not_public_delivery_ready(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Status should not mark a local-only webhook URL as externally ready."""

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
    asyncio.run(
        get_credentials_service(settings).create(
            profile_id="default",
            tool_name="app.run",
            integration_name="partyflow",
            credential_profile_key="ops-local-ready",
            credential_name="partyflow_bot_token",
            secret_value="fri_bot_test",
        )
    )

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-local-ready",
            "--profile",
            "default",
            "--credential-profile",
            "ops-local-ready",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    status_json = runner.invoke(
        app,
        ["channel", "partyflow", "status", "ops-local-ready", "--json"],
    )
    assert status_json.exit_code == 1
    payload = json.loads(status_json.stdout)
    row = payload["partyflow_webhooks"][0]
    assert payload["ok"] is False
    assert row["bot_token_configured"] is True
    assert row["webhook_url"] == _local_partyflow_webhook_url("ops-local-ready")
    assert row["webhook_url_status"] == "local_only"
    assert row["webhook_url_source"] == "local"
    assert row["webhook_url_public_delivery_ready"] is False
    assert "public HTTPS" in row["webhook_url_notice"]

    status_text = runner.invoke(app, ["channel", "partyflow", "status", "ops-local-ready"])
    assert status_text.exit_code == 1
    assert "webhook_url_status=local_only" in status_text.stdout
    assert f"webhook_url: {_local_partyflow_webhook_url('ops-local-ready')}" in status_text.stdout
    assert "webhook_url warning:" in status_text.stdout
    assert "public HTTPS" in status_text.stdout


@pytest.mark.parametrize("runtime_host", ["0.0.0.0", "::1", "192.168.1.10"])
def test_channel_partyflow_local_webhook_url_uses_loopback_for_local_fallback(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    runtime_host: str,
) -> None:
    """Local fallback should always render a copyable loopback URL."""

    monkeypatch.setenv("AFKBOT_RUNTIME_HOST", runtime_host)
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

    created = runner.invoke(
        app,
        [
            "channel",
            "partyflow",
            "add",
            "ops-wildcard-bind",
            "--profile",
            "default",
            "--credential-profile",
            "ops-wildcard-bind",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    shown = runner.invoke(
        app,
        ["channel", "partyflow", "webhook-url", "ops-wildcard-bind"],
    )
    assert shown.exit_code == 0
    assert shown.stdout.strip().startswith("http://127.0.0.1:")
    assert shown.stdout.strip().endswith("/v1/channels/partyflow/ops-wildcard-bind/webhook")


def test_channel_partyflow_status_allows_missing_optional_signing_secret(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow status should remain ok when only optional webhook signature validation is off."""

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
    asyncio.run(
        get_credentials_service(settings).create(
            profile_id="default",
            tool_name="app.run",
            integration_name="partyflow",
            credential_profile_key="ops-no-signature",
            credential_name="partyflow_bot_token",
            secret_value="fri_bot_test",
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
            "ops-no-signature",
            "--profile",
            "default",
            "--credential-profile",
            "ops-no-signature",
            "--no-binding",
            "--yes",
        ],
    )
    assert created.exit_code == 0

    status = runner.invoke(
        app,
        ["channel", "partyflow", "status", "ops-no-signature", "--json"],
    )

    assert status.exit_code == 0
    row = json.loads(status.stdout)["partyflow_webhooks"][0]
    assert row["bot_token_configured"] is True
    assert row["signing_secret_configured"] is False
    assert row["signature_validation"] == "disabled"
    assert "webhook signature validation is disabled" in row["signing_secret_notice"]


def test_channel_partyflow_show_falls_back_to_local_for_private_hostname_suffixes(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """PartyFlow show should not treat private suffixes as public webhook URLs."""

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
    assert f"- webhook_url: {_local_partyflow_webhook_url('ops-private-host')}" in shown
    assert "localhost/private" in shown
    assert "webhook_url uses local AFKBOT Chat API" in shown
