"""Channel access-policy binding projection tests."""

from __future__ import annotations

from afkbot.cli.commands.channel_shared import build_access_policy_binding_rules
from afkbot.services.channels.endpoint_contracts import ChannelAccessPolicy


def test_access_policy_bindings_keep_open_private_with_group_allowlist() -> None:
    """Mixed open/allowlist policy should keep a broad runtime-guarded binding for open DMs."""

    rules = build_access_policy_binding_rules(
        endpoint_id="owner-bot",
        transport="telegram",
        profile_id="default",
        session_policy="per-chat",
        priority=0,
        enabled=True,
        account_id="bot-main",
        prompt_overlay=None,
        access_policy=ChannelAccessPolicy(
            private_policy="open",
            group_policy="allowlist",
            groups=("-100123",),
            group_allow_from=("12345",),
        ),
    )

    assert [(item.binding_id, item.peer_id, item.user_id) for item in rules] == [
        ("owner-bot:group:-100123:user:12345", "-100123", "12345"),
        ("owner-bot", None, None),
    ]


def test_access_policy_bindings_keep_open_groups_with_private_allowlist() -> None:
    """Mixed allowlist/open policy should keep a broad runtime-guarded binding for open groups."""

    rules = build_access_policy_binding_rules(
        endpoint_id="owner-bot",
        transport="telegram",
        profile_id="default",
        session_policy="per-chat",
        priority=0,
        enabled=True,
        account_id="bot-main",
        prompt_overlay=None,
        access_policy=ChannelAccessPolicy(
            private_policy="allowlist",
            allow_from=("12345",),
            group_policy="open",
        ),
    )

    assert [(item.binding_id, item.peer_id, item.user_id) for item in rules] == [
        ("owner-bot:dm:12345", "12345", "12345"),
        ("owner-bot", None, None),
    ]


def test_access_policy_bindings_use_sender_only_private_partyflow_rules() -> None:
    """PartyFlow DM conversation ids are not guaranteed to equal user ids."""

    rules = build_access_policy_binding_rules(
        endpoint_id="partyflow-main",
        transport="partyflow",
        profile_id="default",
        session_policy="per-chat",
        priority=0,
        enabled=True,
        account_id="partyflow-bot",
        prompt_overlay=None,
        access_policy=ChannelAccessPolicy(
            private_policy="allowlist",
            allow_from=("user-1",),
            group_policy="disabled",
        ),
    )

    assert [(item.binding_id, item.peer_id, item.user_id) for item in rules] == [
        ("partyflow-main:dm:user-1", None, "user-1"),
    ]
