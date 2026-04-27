"""Channel access-policy evaluation helpers."""

from __future__ import annotations

from afkbot.services.channels.endpoint_contracts import ChannelAccessPolicy

_GROUP_KINDS = {"group", "supergroup", "channel"}


def is_channel_message_allowed(
    *,
    policy: ChannelAccessPolicy,
    chat_kind: str | None,
    peer_id: str | None,
    user_id: str | None,
) -> bool:
    """Return whether one inbound channel event may reach routing/AgentLoop."""

    normalized_kind = (chat_kind or "").strip().lower()
    if normalized_kind == "private":
        return _is_private_allowed(policy=policy, peer_id=peer_id, user_id=user_id)
    if normalized_kind in _GROUP_KINDS:
        return _is_group_allowed(policy=policy, peer_id=peer_id, user_id=user_id)
    return _is_group_allowed(policy=policy, peer_id=peer_id, user_id=user_id)


def _is_private_allowed(
    *,
    policy: ChannelAccessPolicy,
    peer_id: str | None,
    user_id: str | None,
) -> bool:
    if policy.private_policy == "disabled":
        return False
    if policy.private_policy == "open":
        return True
    allowed = set(policy.allow_from)
    return "*" in allowed or _matches_any(allowed=allowed, values=(user_id, peer_id))


def _is_group_allowed(
    *,
    policy: ChannelAccessPolicy,
    peer_id: str | None,
    user_id: str | None,
) -> bool:
    if policy.group_policy == "disabled":
        return False
    if policy.groups and "*" not in policy.groups and peer_id not in policy.groups:
        return False
    if policy.group_policy == "open":
        return True
    allowed_senders = set(policy.group_allow_from or policy.allow_from)
    return "*" in allowed_senders or _matches_any(allowed=allowed_senders, values=(user_id,))


def _matches_any(*, allowed: set[str], values: tuple[str | None, ...]) -> bool:
    return any(value is not None and value in allowed for value in values)
