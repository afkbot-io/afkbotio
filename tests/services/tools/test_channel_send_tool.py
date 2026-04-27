"""Tests for channel.send tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.channels import ChannelDeliveryTarget
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


@pytest.mark.asyncio
async def test_channel_send_tool_delivers_text_to_explicit_telegram_target(tmp_path: Path) -> None:
    """channel.send should expose outbound channel delivery as a normal tool."""

    delivery = _FakeDeliveryService()
    tool = ChannelSendTool(
        Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_send.db'}"),
        delivery_service=delivery,  # type: ignore[arg-type]
    )
    ctx = ToolContext(profile_id="default", session_id="main", run_id=7)

    result = await tool.execute(
        ctx,
        ChannelSendParams(
            transport="telegram",
            account_id="bot-main",
            chat_id="12345",
            text="hello",
            credential_profile_key="bot-main",
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
