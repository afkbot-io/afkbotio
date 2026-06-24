"""Channel access-policy contract tests."""

from __future__ import annotations

import pytest

from afkbot.services.channels.access_policy import is_channel_message_allowed
from afkbot.services.channels.endpoint_contracts import ChannelAccessPolicy, ChannelEndpointConfig
from afkbot.services.channels.tool_profiles import (
    allowed_tool_names_for_channel_profile,
    default_channel_tool_profile_for_policy,
)
from afkbot.services.profile_runtime.contracts import ProfilePolicyView


def test_channel_access_policy_allows_only_configured_private_sender() -> None:
    """DM allowlist should accept only explicit sender ids."""

    policy = ChannelAccessPolicy(private_policy="allowlist", allow_from=("12345",))

    assert (
        is_channel_message_allowed(
            policy=policy,
            chat_kind="private",
            peer_id="12345",
            user_id="12345",
        )
        is True
    )
    assert (
        is_channel_message_allowed(
            policy=policy,
            chat_kind="private",
            peer_id="67890",
            user_id="67890",
        )
        is False
    )


def test_channel_access_policy_defaults_deny_inbound_messages() -> None:
    """Empty or missing channel policies should fail closed instead of becoming public."""

    policy = ChannelAccessPolicy()

    assert (
        is_channel_message_allowed(
            policy=policy,
            chat_kind="private",
            peer_id="12345",
            user_id="12345",
        )
        is False
    )
    assert (
        is_channel_message_allowed(
            policy=policy,
            chat_kind="supergroup",
            peer_id="-100123",
            user_id="12345",
        )
        is False
    )


def test_channel_endpoint_config_missing_access_policy_defaults_deny() -> None:
    """Legacy/imported endpoints without access_policy should not become open channels."""

    endpoint = ChannelEndpointConfig.model_validate(
        {
            "endpoint_id": "telegram-main",
            "transport": "telegram",
            "adapter_kind": "telegram",
            "profile_id": "default",
            "credential_profile_key": "default",
            "account_id": "default",
        }
    )

    assert endpoint.access_policy.private_policy == "disabled"
    assert endpoint.access_policy.group_policy == "disabled"


def test_channel_access_policy_requires_allowed_group_and_sender() -> None:
    """Group allowlists should gate both the group id and the sender id."""

    policy = ChannelAccessPolicy(
        group_policy="allowlist",
        groups=("-100123",),
        group_allow_from=("12345",),
    )

    assert (
        is_channel_message_allowed(
            policy=policy,
            chat_kind="supergroup",
            peer_id="-100123",
            user_id="12345",
        )
        is True
    )
    assert (
        is_channel_message_allowed(
            policy=policy,
            chat_kind="supergroup",
            peer_id="-100999",
            user_id="12345",
        )
        is False
    )
    assert (
        is_channel_message_allowed(
            policy=policy,
            chat_kind="supergroup",
            peer_id="-100123",
            user_id="99999",
        )
        is False
    )


def test_channel_access_policy_rejects_empty_private_allowlist() -> None:
    """Allowlist mode should not silently become open when no ids are configured."""

    with pytest.raises(ValueError, match="private allowlist"):
        ChannelAccessPolicy(private_policy="allowlist")


def test_taskflow_channel_tool_profile_is_intake_only() -> None:
    """Task Flow channel operators should not receive employee-level docs or delegation tools."""

    allowed_tools = allowed_tool_names_for_channel_profile("taskflow_operator") or ()

    assert "task.create" in allowed_tools
    assert "task.comment.add" in allowed_tools
    assert "task.flow.list" in allowed_tools
    assert "task.context.get" not in allowed_tools
    assert "task.doc.list" not in allowed_tools
    assert "task.doc.put" not in allowed_tools
    assert "task.doc.confirm" not in allowed_tools
    assert "task.delegate" not in allowed_tools
    assert "task.update" not in allowed_tools
    assert "bash.exec" not in allowed_tools


def test_default_channel_tool_profile_does_not_grant_file_tools_when_policy_disabled() -> None:
    """Disabled profile policy should not make user channels default to file-readable tools."""

    policy = ProfilePolicyView(enabled=False)

    selected = default_channel_tool_profile_for_policy(policy=policy)
    allowed_tools = allowed_tool_names_for_channel_profile(selected) or ()

    assert selected == "messaging_safe"
    assert "memory.search" in allowed_tools
    assert "file.read" not in allowed_tools
    assert "file.search" not in allowed_tools


def test_default_channel_tool_profile_never_auto_grants_profile_file_or_taskflow_tools() -> None:
    """User-facing channel defaults must stay safe even when the profile has broader power."""

    policy = ProfilePolicyView(
        enabled=True,
        capabilities=("memory", "files", "taskflow"),
        file_access_mode="read_write",
    )

    selected = default_channel_tool_profile_for_policy(policy=policy)
    allowed_tools = allowed_tool_names_for_channel_profile(selected) or ()

    assert selected == "messaging_safe"
    assert "memory.search" in allowed_tools
    assert "file.list" not in allowed_tools
    assert "file.read" not in allowed_tools
    assert "file.search" not in allowed_tools
    assert "task.create" not in allowed_tools
