"""Tests for channel.send tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.channels import ChannelDeliveryTarget
from afkbot.services.channels.endpoint_contracts import (
    ChannelAccessPolicy,
    TelegramPollingEndpointConfig,
)
from afkbot.services.channels.endpoint_service import ChannelEndpointServiceError
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.plugins.channel_send.plugin import ChannelSendParams, ChannelSendTool
from afkbot.settings import Settings


class _FakeDeliveryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def deliver_text(
        self,
        *,
        profile_id: str,
        session_id: str,
        run_id: int,
        target: ChannelDeliveryTarget,
        text: str,
        credential_profile_key: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "run_id": run_id,
                "target": target,
                "text": text,
                "credential_profile_key": credential_profile_key,
            }
        )
        return {"ok": True, "transport": target.transport}


class _FakeEndpointService:
    def __init__(
        self,
        endpoint: TelegramPollingEndpointConfig | None = None,
        endpoints: list[TelegramPollingEndpointConfig] | None = None,
    ) -> None:
        self.endpoints = list(endpoints or ([] if endpoint is None else [endpoint]))

    async def get(self, *, endpoint_id: str) -> TelegramPollingEndpointConfig:
        for endpoint in self.endpoints:
            if endpoint.endpoint_id == endpoint_id:
                return endpoint
        raise ChannelEndpointServiceError(
            error_code="channel_endpoint_not_found",
            reason=f"Channel endpoint not found: {endpoint_id}",
        )

    async def list(
        self,
        *,
        transport: str | None = None,
        enabled: bool | None = None,
        profile_id: str | None = None,
    ) -> list[TelegramPollingEndpointConfig]:
        endpoints = self.endpoints
        if transport is not None:
            endpoints = [item for item in endpoints if item.transport == transport]
        if enabled is not None:
            endpoints = [item for item in endpoints if item.enabled == enabled]
        if profile_id is not None:
            endpoints = [item for item in endpoints if item.profile_id == profile_id]
        return endpoints


@pytest.mark.asyncio
async def test_channel_send_tool_delivers_text_to_explicit_telegram_target(tmp_path: Path) -> None:
    """channel.send should expose outbound channel delivery as a normal tool."""

    delivery = _FakeDeliveryService()
    endpoint = TelegramPollingEndpointConfig(
        endpoint_id="owner-bot",
        profile_id="default",
        credential_profile_key="bot-main",
        account_id="bot-main",
    )
    tool = ChannelSendTool(
        Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_send.db'}"),
        delivery_service=delivery,  # type: ignore[arg-type]
        endpoint_service=_FakeEndpointService(endpoint),  # type: ignore[arg-type]
    )
    ctx = ToolContext(profile_id="default", session_id="main", run_id=7)

    result = await tool.execute(
        ctx,
        ChannelSendParams(
            transport="telegram",
            account_id="bot-main",
            chat_id="12345",
            text="hello",
        ),
    )

    assert result.ok is True
    assert delivery.calls[0]["text"] == "hello"
    assert delivery.calls[0]["credential_profile_key"] == "bot-main"
    assert delivery.calls[0]["target"].model_dump(exclude_none=True) == {
        "transport": "telegram",
        "account_id": "bot-main",
        "peer_id": "12345",
    }


@pytest.mark.asyncio
async def test_channel_send_tool_requires_text_for_now(tmp_path: Path) -> None:
    """MVP channel.send should fail clearly when no text payload is provided."""

    tool = ChannelSendTool(
        Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_send_empty.db'}")
    )
    ctx = ToolContext(profile_id="default", session_id="main", run_id=8)

    result = await tool.execute(
        ctx,
        ChannelSendParams(transport="telegram", chat_id="12345", text=""),
    )

    assert result.ok is False
    assert result.error_code == "channel_send_text_required"


@pytest.mark.asyncio
async def test_channel_send_tool_enforces_endpoint_outbound_allowlist(tmp_path: Path) -> None:
    """channel.send should not bypass channel-level outbound target restrictions."""

    delivery = _FakeDeliveryService()
    endpoint = TelegramPollingEndpointConfig(
        endpoint_id="owner-bot",
        profile_id="default",
        credential_profile_key="bot-main",
        account_id="bot-main",
        access_policy=ChannelAccessPolicy(outbound_allow_to=("12345",)),
    )
    tool = ChannelSendTool(
        Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_send_policy.db'}"),
        delivery_service=delivery,  # type: ignore[arg-type]
        endpoint_service=_FakeEndpointService(endpoint),  # type: ignore[arg-type]
    )
    ctx = ToolContext(profile_id="default", session_id="main", run_id=9)

    result = await tool.execute(
        ctx,
        ChannelSendParams(
            transport="telegram",
            account_id="bot-main",
            chat_id="99999",
            text="blocked",
        ),
    )

    assert result.ok is False
    assert result.error_code == "channel_send_target_not_allowed"
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_channel_send_tool_uses_binding_endpoint_for_allowlist(tmp_path: Path) -> None:
    """binding_id-derived endpoint ids should still run outbound allowlist checks."""

    delivery = _FakeDeliveryService()
    endpoint = TelegramPollingEndpointConfig(
        endpoint_id="owner-bot",
        profile_id="default",
        credential_profile_key="bot-main",
        account_id="bot-main",
        access_policy=ChannelAccessPolicy(outbound_allow_to=("12345",)),
    )
    tool = ChannelSendTool(
        Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_send_binding.db'}"),
        delivery_service=delivery,  # type: ignore[arg-type]
        endpoint_service=_FakeEndpointService(endpoint),  # type: ignore[arg-type]
    )
    ctx = ToolContext(profile_id="default", session_id="main", run_id=10)

    result = await tool.execute(
        ctx,
        ChannelSendParams(
            transport="telegram",
            binding_id="owner-bot:dm:12345",
            account_id="bot-main",
            chat_id="99999",
            text="blocked",
        ),
    )

    assert result.ok is False
    assert result.error_code == "channel_send_target_not_allowed"
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_channel_send_tool_fails_closed_when_endpoint_is_ambiguous(tmp_path: Path) -> None:
    """Multiple matching endpoints should require endpoint_id instead of skipping policy checks."""

    delivery = _FakeDeliveryService()
    endpoints = [
        TelegramPollingEndpointConfig(
            endpoint_id="owner-bot",
            profile_id="default",
            credential_profile_key="bot-main",
            account_id="bot-main",
            access_policy=ChannelAccessPolicy(outbound_allow_to=("12345",)),
        ),
        TelegramPollingEndpointConfig(
            endpoint_id="support-bot",
            profile_id="default",
            credential_profile_key="support-main",
            account_id="support-main",
            access_policy=ChannelAccessPolicy(outbound_allow_to=("67890",)),
        ),
    ]
    tool = ChannelSendTool(
        Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_send_ambiguous.db'}"),
        delivery_service=delivery,  # type: ignore[arg-type]
        endpoint_service=_FakeEndpointService(endpoints=endpoints),  # type: ignore[arg-type]
    )
    ctx = ToolContext(profile_id="default", session_id="main", run_id=11)

    result = await tool.execute(
        ctx,
        ChannelSendParams(transport="telegram", chat_id="99999", text="blocked"),
    )

    assert result.ok is False
    assert result.error_code == "channel_send_endpoint_ambiguous"
    assert delivery.calls == []


@pytest.mark.asyncio
async def test_channel_send_tool_rejects_smtp_transport(tmp_path: Path) -> None:
    """channel.send should not re-open arbitrary SMTP delivery on safe channel profiles."""

    delivery = _FakeDeliveryService()
    tool = ChannelSendTool(
        Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_send_smtp.db'}"),
        delivery_service=delivery,  # type: ignore[arg-type]
        endpoint_service=_FakeEndpointService(),  # type: ignore[arg-type]
    )
    ctx = ToolContext(profile_id="default", session_id="main", run_id=12)

    result = await tool.execute(
        ctx,
        ChannelSendParams(transport="smtp", address="ops@example.com", text="blocked"),
    )

    assert result.ok is False
    assert result.error_code == "channel_send_transport_not_supported"
    assert delivery.calls == []
