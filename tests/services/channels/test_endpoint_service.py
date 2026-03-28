"""Tests for channel endpoint persistence helpers."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service
from afkbot.services.channels.ingress_journal import get_channel_ingress_journal_service
from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.service import ProfileService
from afkbot.settings import Settings


async def test_channel_endpoint_delete_removes_ingress_journal_rows(tmp_path: Path) -> None:
    """Deleting an endpoint should remove its ingress journal rows first."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'endpoint.db'}",
    )
    profiles = ProfileService(settings)
    endpoints = get_channel_endpoint_service(settings)
    journal = get_channel_ingress_journal_service(settings)
    try:
        await profiles.create(
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
        await endpoints.create(
            TelethonUserEndpointConfig(
                endpoint_id="personal-user",
                profile_id="default",
                credential_profile_key="tg-user",
                account_id="tg-user",
            )
        )
        assert await journal.record_processed(
            endpoint_id="personal-user",
            transport="telegram_user",
            event_key="tg-user:42:5",
        ) is True

        deleted = await endpoints.delete(endpoint_id="personal-user")

        assert deleted is True
        assert await journal.contains(endpoint_id="personal-user", event_key="tg-user:42:5") is False
    finally:
        await profiles.shutdown()
        await endpoints.shutdown()
        await journal.shutdown()


def test_get_channel_endpoint_service_returns_fresh_service_outside_running_loop(tmp_path: Path) -> None:
    """Sync CLI call-sites should not reuse one endpoint service across different loops."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'endpoint_registry.db'}",
    )

    first = get_channel_endpoint_service(settings)
    second = get_channel_endpoint_service(settings)

    assert first is not second
