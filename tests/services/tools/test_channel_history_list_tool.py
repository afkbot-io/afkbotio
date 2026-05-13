"""Tests for channel.history.list tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.agent_loop.turn_context import merge_turn_context_overrides
from afkbot.services.channels.active_context import build_active_channel_context_overrides
from afkbot.services.channels.endpoint_contracts import PartyFlowPollingEndpointConfig
from afkbot.services.channels.endpoint_service import ChannelEndpointServiceError
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.plugins.channel_history_list.plugin import (
    ChannelHistoryListParams,
    ChannelHistoryListTool,
)
from afkbot.settings import Settings


class _FakeEndpointService:
    def __init__(self, endpoint: PartyFlowPollingEndpointConfig) -> None:
        self.endpoint = endpoint

    async def get(self, *, endpoint_id: str) -> PartyFlowPollingEndpointConfig:
        if endpoint_id == self.endpoint.endpoint_id:
            return self.endpoint
        raise ChannelEndpointServiceError(
            error_code="channel_endpoint_not_found",
            reason=f"Channel endpoint not found: {endpoint_id}",
        )


class _FakeCredentialsService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def resolve_plaintext_for_app_tool(self, **kwargs: object) -> str:
        self.calls.append(dict(kwargs))
        return "fri_bot_test_token"


@pytest.mark.asyncio
async def test_channel_history_list_reads_active_partyflow_conversation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Active PartyFlow turns should infer endpoint, conversation, and thread safely."""

    endpoint = PartyFlowPollingEndpointConfig(
        endpoint_id="partyflow-main",
        profile_id="default",
        credential_profile_key="partyflow-main",
        account_id="partyflow-bot",
    )
    credentials = _FakeCredentialsService()
    calls: list[dict[str, object]] = []

    async def _fake_get_messages(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {"messages": [{"id": "msg-1"}], "has_more": False}

    monkeypatch.setattr(
        "afkbot.services.tools.plugins.channel_history_list.plugin.get_credentials_service",
        lambda settings: credentials,
    )
    monkeypatch.setattr(
        "afkbot.services.tools.plugins.channel_history_list.plugin._get_messages",
        _fake_get_messages,
    )
    overrides = merge_turn_context_overrides(
        build_active_channel_context_overrides(
            endpoint=endpoint,
            peer_id="conv-1",
            thread_id="thread-1",
            user_id="user-1",
        )
    )
    assert overrides is not None
    tool = ChannelHistoryListTool(
        Settings(
            root_dir=tmp_path,
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_history.db'}",
        ),
        endpoint_service=_FakeEndpointService(endpoint),  # type: ignore[arg-type]
    )
    ctx = ToolContext(
        profile_id="default",
        session_id="s",
        run_id=1,
        trusted_runtime_context=overrides.trusted_runtime_context,
    )

    result = await tool.execute(ctx, ChannelHistoryListParams(limit=25, around_msg_index=42))

    assert result.ok is True
    assert result.payload["messages"] == [{"id": "msg-1"}]
    assert result.payload["endpoint_id"] == "partyflow-main"
    assert result.payload["conversation_id"] == "conv-1"
    assert result.payload["thread_id"] == "thread-1"
    assert credentials.calls == [
        {
            "profile_id": "default",
            "tool_name": "app.run",
            "integration_name": "partyflow",
            "credential_profile_key": "partyflow-main",
            "credential_name": "partyflow_bot_token",
        }
    ]
    assert calls == [
        {
            "base_url": "https://api.partyflow.ru",
            "token": "fri_bot_test_token",
            "conversation_id": "conv-1",
            "limit": 25,
            "before_msg_index": None,
            "after_msg_index": None,
            "around_msg_index": 42,
            "updated_since": None,
            "thread_id": "thread-1",
            "timeout_sec": 15,
        }
    ]


@pytest.mark.asyncio
async def test_channel_history_list_rejects_non_active_endpoint(
    tmp_path: Path,
) -> None:
    """An active channel turn must not read another endpoint's history."""

    endpoint = PartyFlowPollingEndpointConfig(
        endpoint_id="partyflow-main",
        profile_id="default",
        credential_profile_key="partyflow-main",
        account_id="partyflow-bot",
    )
    overrides = merge_turn_context_overrides(
        build_active_channel_context_overrides(
            endpoint=endpoint,
            peer_id="conv-1",
            thread_id=None,
            user_id="user-1",
        )
    )
    assert overrides is not None
    tool = ChannelHistoryListTool(
        Settings(
            root_dir=tmp_path,
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'channel_history_reject.db'}",
        ),
        endpoint_service=_FakeEndpointService(endpoint),  # type: ignore[arg-type]
    )
    ctx = ToolContext(
        profile_id="default",
        session_id="s",
        run_id=1,
        trusted_runtime_context=overrides.trusted_runtime_context,
    )

    result = await tool.execute(
        ctx,
        ChannelHistoryListParams(endpoint_id="other-partyflow", conversation_id="conv-1"),
    )

    assert result.ok is False
    assert result.error_code == "channel_history_endpoint_not_active"
