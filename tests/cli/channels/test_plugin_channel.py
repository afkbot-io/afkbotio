"""Generic plugin channel CLI tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.plugins import get_plugin_service, scaffold_plugin
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.settings import get_settings
from tests.cli.channels._harness import _new_profile_service, _prepare_env


def _prepare_plugin_channel_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _prepare_env(tmp_path, monkeypatch)
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
            policy_network_allowlist=(),
        )
    )
    plugin_root = tmp_path / "plugins" / "avito"
    scaffold_plugin(
        destination=plugin_root,
        plugin_id="avito",
        name="Avito Channel",
        channel=True,
        api_router=False,
        static_web=False,
    )
    get_plugin_service(settings).install(source=str(plugin_root))


def test_channel_plugin_add_persists_generic_endpoint(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _prepare_plugin_channel_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()

    adapters_result = runner.invoke(app, ["channel", "plugin", "adapters", "--json"])

    assert adapters_result.exit_code == 0
    assert '"transport": "avito"' in adapters_result.stdout

    result = runner.invoke(
        app,
        [
            "channel",
            "plugin",
            "add",
            "avito-main",
            "--transport",
            "avito",
            "--adapter-kind",
            "avito_polling",
            "--profile",
            "default",
            "--credential-profile",
            "avito-main",
            "--account-id",
            "seller-1",
            "--allow-from",
            "buyer-1",
            "--outbound-allow-to",
            "conv-1",
            "--config-json",
            '{"poll_interval_sec": 30}',
        ],
    )

    assert result.exit_code == 0
    assert "Plugin channel `avito-main` created." in result.stdout
    endpoint = asyncio.run(get_channel_endpoint_service(settings).get(endpoint_id="avito-main"))
    assert isinstance(endpoint, ChannelEndpointConfig)
    assert endpoint.transport == "avito"
    assert endpoint.adapter_kind == "avito_polling"
    assert endpoint.profile_id == "default"
    assert endpoint.credential_profile_key == "avito-main"
    assert endpoint.account_id == "seller-1"
    assert endpoint.access_policy.allow_from == ("buyer-1",)
    assert endpoint.access_policy.outbound_allow_to == ("conv-1",)
    assert endpoint.config == {"poll_interval_sec": 30}


def test_channel_plugin_add_applies_adapter_schema_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _prepare_plugin_channel_env(tmp_path, monkeypatch)
    runner = CliRunner()
    settings = get_settings()

    result = runner.invoke(
        app,
        [
            "channel",
            "plugin",
            "add",
            "avito-main",
            "--transport",
            "avito",
            "--adapter-kind",
            "avito_polling",
            "--profile",
            "default",
            "--credential-profile",
            "avito-main",
            "--allow-from",
            "buyer-1",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    endpoint = asyncio.run(get_channel_endpoint_service(settings).get(endpoint_id="avito-main"))
    assert isinstance(endpoint, ChannelEndpointConfig)
    assert endpoint.config == {"poll_interval_sec": 30}


def test_channel_plugin_add_rejects_invalid_adapter_config(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _prepare_plugin_channel_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "channel",
            "plugin",
            "add",
            "avito-main",
            "--transport",
            "avito",
            "--adapter-kind",
            "avito_polling",
            "--credential-profile",
            "avito-main",
            "--allow-from",
            "buyer-1",
            "--config-json",
            '{"poll_interval_sec": 1}',
        ],
    )

    assert result.exit_code == 2
    assert "poll_interval_sec" in result.stderr


def test_channel_plugin_add_reports_missing_adapter_without_error_text_loss(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    _prepare_env(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "channel",
            "plugin",
            "add",
            "avito-main",
            "--transport",
            "avito",
            "--adapter-kind",
            "avito_polling",
            "--credential-profile",
            "avito-main",
            "--allow-from",
            "buyer-1",
        ],
    )

    assert result.exit_code == 2
    assert "Plugin channel adapter is not installed and enabled" in result.stderr
    assert "afk channel plugin adapters" in result.stderr


def test_channel_plugin_lifecycle_with_installed_adapter(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
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
    scaffold = scaffold_plugin(
        destination=tmp_path / "plugins" / "avito-channel",
        plugin_id="avito",
        name="Avito Channel",
        api_router=False,
        static_web=False,
        channel=True,
    )
    get_plugin_service(settings).install(source=str(scaffold.plugin_root), overwrite=True)

    adapters = runner.invoke(app, ["channel", "plugin", "adapters", "--json"])

    assert adapters.exit_code == 0
    adapter_payload = json.loads(adapters.stdout)
    assert adapter_payload["adapters"][0]["transport"] == "avito"
    assert adapter_payload["adapters"][0]["validates_endpoint_config"] is True
    assert "poll_interval_sec" in adapter_payload["adapters"][0]["endpoint_config_schema"]

    invalid = runner.invoke(
        app,
        [
            "channel",
            "plugin",
            "add",
            "avito-main",
            "--transport",
            "avito",
            "--adapter-kind",
            "avito_polling",
            "--credential-profile",
            "avito-main",
            "--allow-from",
            "buyer-1",
            "--config-json",
            '{"poll_interval_sec": 3}',
        ],
    )
    assert invalid.exit_code == 2
    assert "poll_interval_sec" in invalid.stderr

    created = runner.invoke(
        app,
        [
            "channel",
            "plugin",
            "add",
            "avito-main",
            "--transport",
            "avito",
            "--adapter-kind",
            "avito_polling",
            "--credential-profile",
            "avito-main",
            "--allow-from",
            "buyer-1",
            "--outbound-allow-to",
            "conv-1",
            "--config-json",
            '{"poll_interval_sec": 30}',
        ],
    )
    assert created.exit_code == 0

    disabled = runner.invoke(app, ["channel", "disable", "avito-main", "--json"])
    assert disabled.exit_code == 0
    assert json.loads(disabled.stdout)["channel"]["enabled"] is False

    enabled = runner.invoke(app, ["channel", "enable", "avito-main", "--json"])
    assert enabled.exit_code == 0
    assert json.loads(enabled.stdout)["channel"]["enabled"] is True

    deleted = runner.invoke(app, ["channel", "delete", "avito-main", "--json"])
    assert deleted.exit_code == 0
    assert json.loads(deleted.stdout) == {"deleted": True, "channel_id": "avito-main"}

    missing = runner.invoke(app, ["channel", "show", "avito-main"])
    assert missing.exit_code == 2
    assert "channel_endpoint_not_found" in missing.stderr
