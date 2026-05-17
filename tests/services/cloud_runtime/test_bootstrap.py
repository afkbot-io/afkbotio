"""Tests for managed Cloud runtime bootstrap materialization."""

from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet

from afkbot.services.channel_routing import ChannelRoutingInput, get_channel_binding_service
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.cloud_runtime.bootstrap import apply_managed_cloud_manifest
from afkbot.services.credentials import get_credentials_service
from afkbot.services.automations import get_automations_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig, get_profile_runtime_config_service
from afkbot.settings import Settings
from tests.services.automations._harness import FakeLoop


async def test_managed_cloud_manifest_bootstraps_telegram_channel(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Cloud startup should create the same runtime rows a channel wizard creates."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'cloud-bootstrap.db'}",
        deployment_mode="managed",
        control_ws_url="wss://cloud.example.test/ws/runtime/connect/",
        runtime_ws_token="test-token",
        credentials_master_keys=Fernet.generate_key().decode("utf-8"),
    )
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "408879132")
    runtime_configs = get_profile_runtime_config_service(settings)
    runtime_configs.write(
        "default",
        ProfileRuntimeConfig(
            llm_provider="openrouter",
            llm_model="auto",
            enabled_tool_plugins=("memory_search", "channel_send"),
        ),
    )
    manifest_path = runtime_configs.system_dir("default") / "cloud_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "channels": [
                        {
                            "access_policy": {
                                "allow_from": [],
                                "group_policy": "disabled",
                                "private_policy": "allowlist",
                            },
                            "account_id": "support-bot",
                        "credential_profile_key": "support-bot",
                        "enabled": True,
                        "endpoint_id": "telegram-support",
                        "kind": "telegram_bot",
                        "secret_refs": {
                            "TELEGRAM_BOT_TOKEN": "TELEGRAM_BOT_TOKEN",
                            "TELEGRAM_CHAT_ID": "TELEGRAM_CHAT_ID",
                        },
                        "session_policy": "per-chat",
                        "tool_profile": "messaging_safe",
                    }
                ],
                "automations": [
                    {
                        "enabled": True,
                        "id": "incident-hook",
                        "name": "Incident hook",
                        "prompt": "Handle incident payload.",
                        "trigger_type": "webhook",
                        "webhook_token": "cloud-hook-token",
                    },
                    {
                        "cron_expr": "*/5 * * * *",
                        "enabled": True,
                        "id": "health-check",
                        "name": "Health check",
                        "prompt": "Run health check.",
                        "timezone_name": "UTC",
                        "trigger_type": "cron",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    await apply_managed_cloud_manifest(settings=settings, profile_id="default")

    endpoint = await get_channel_endpoint_service(settings).get(endpoint_id="telegram-support")
    token = await get_credentials_service(settings).resolve_plaintext_for_app_tool(
        profile_id="default",
        tool_name="app.run",
        integration_name="telegram",
        credential_profile_key="support-bot",
        credential_name="telegram_token",
    )
    chat_id = await get_credentials_service(settings).resolve_plaintext_for_app_tool(
        profile_id="default",
        tool_name="app.run",
        integration_name="telegram",
        credential_profile_key="support-bot",
        credential_name="telegram_chat_id",
    )
    decision = await get_channel_binding_service(settings).resolve(
        routing_input=ChannelRoutingInput(
            transport="telegram",
            account_id="support-bot",
            peer_id="408879132",
            default_session_id="main",
        )
    )
    automations = await get_automations_service(settings).list(profile_id="default")
    webhook = next(item for item in automations if item.name == "Incident hook")
    cron = next(item for item in automations if item.name == "Health check")

    assert endpoint.profile_id == "default"
    assert endpoint.credential_profile_key == "support-bot"
    assert endpoint.access_policy.private_policy == "allowlist"
    assert endpoint.access_policy.allow_from == ("408879132",)
    assert token == "123:token"
    assert chat_id == "408879132"
    assert decision is not None
    assert decision.profile_id == "default"
    assert decision.session_id == "profile:default:chat:408879132"
    assert webhook.webhook is not None
    fake_loop = FakeLoop()
    webhook_result = await get_automations_service(settings).trigger_webhook(
        profile_id="default",
        token="cloud-hook-token",
        payload={"event_id": "evt-1"},
        session_runner_factory=lambda session, profile_id: fake_loop,
    )
    assert webhook_result.automation_id == webhook.id
    assert fake_loop.calls
    assert cron.cron is not None
    assert cron.cron.cron_expr == "*/5 * * * *"
