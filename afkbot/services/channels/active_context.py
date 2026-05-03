"""Trusted active-channel context for channel-scoped tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.channels.endpoint_contracts import ChannelEndpointConfig

ACTIVE_CHANNEL_CONTEXT_KEY = "active_channel"
MESSAGING_ACTIVE_CHANNEL_TOOL_NAMES: tuple[str, ...] = ("channel.send",)
PARTYFLOW_ACTIVE_CHANNEL_TOOL_NAMES: tuple[str, ...] = (
    "channel.history.list",
    *MESSAGING_ACTIVE_CHANNEL_TOOL_NAMES,
)
RESERVED_CHANNEL_OWNED_TOOL_NAMES: frozenset[str] = frozenset(
    (*PARTYFLOW_ACTIVE_CHANNEL_TOOL_NAMES, *MESSAGING_ACTIVE_CHANNEL_TOOL_NAMES)
)


@dataclass(frozen=True, slots=True)
class ActiveChannelContext:
    """Current inbound channel coordinates trusted by the channel runtime."""

    endpoint_id: str
    transport: str
    profile_id: str
    credential_profile_key: str
    account_id: str
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None

    def to_payload(self) -> dict[str, str]:
        payload = {
            "endpoint_id": self.endpoint_id,
            "transport": self.transport,
            "profile_id": self.profile_id,
            "credential_profile_key": self.credential_profile_key,
            "account_id": self.account_id,
            "peer_id": self.peer_id,
            "thread_id": self.thread_id,
            "user_id": self.user_id,
        }
        return {key: value for key, value in payload.items() if value is not None}


def build_active_channel_context_overrides(
    *,
    endpoint: ChannelEndpointConfig,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> TurnContextOverrides | None:
    """Expose trusted current-channel coordinates and reserved channel tools."""

    context = ActiveChannelContext(
        endpoint_id=endpoint.endpoint_id,
        transport=endpoint.transport,
        profile_id=endpoint.profile_id,
        credential_profile_key=endpoint.credential_profile_key,
        account_id=endpoint.account_id,
        peer_id=_normalize_optional(peer_id),
        thread_id=_normalize_optional(thread_id),
        user_id=_normalize_optional(user_id),
    )
    approved_tool_names = channel_owned_tool_names_for_transport(endpoint.transport)
    return TurnContextOverrides(
        trusted_runtime_context={ACTIVE_CHANNEL_CONTEXT_KEY: context.to_payload()},
        channel_owned_tool_names=approved_tool_names or None,
    )


def active_channel_context_from_trusted(
    trusted_runtime_context: Mapping[str, object] | None,
) -> ActiveChannelContext | None:
    """Parse active-channel context from trusted runtime storage."""

    if not isinstance(trusted_runtime_context, Mapping):
        return None
    raw_context = trusted_runtime_context.get(ACTIVE_CHANNEL_CONTEXT_KEY)
    if not isinstance(raw_context, Mapping):
        return None
    required = {
        key: _normalize_optional(raw_context.get(key))
        for key in (
            "endpoint_id",
            "transport",
            "profile_id",
            "credential_profile_key",
            "account_id",
        )
    }
    if not all(required.values()):
        return None
    return ActiveChannelContext(
        endpoint_id=str(required["endpoint_id"]),
        transport=str(required["transport"]).lower(),
        profile_id=str(required["profile_id"]),
        credential_profile_key=str(required["credential_profile_key"]),
        account_id=str(required["account_id"]),
        peer_id=_normalize_optional(raw_context.get("peer_id")),
        thread_id=_normalize_optional(raw_context.get("thread_id")),
        user_id=_normalize_optional(raw_context.get("user_id")),
    )


def channel_owned_tool_names_for_transport(transport: str) -> tuple[str, ...]:
    """Return channel-owned tools that may bypass the profile allowlist for active turns."""

    normalized = transport.strip().lower()
    if normalized == "partyflow":
        return PARTYFLOW_ACTIVE_CHANNEL_TOOL_NAMES
    if normalized in {"telegram", "telegram_user"}:
        return MESSAGING_ACTIVE_CHANNEL_TOOL_NAMES
    return ()


def filter_channel_owned_approved_tool_names(
    *,
    trusted_runtime_context: Mapping[str, object] | None,
    approved_tool_names: set[str] | tuple[str, ...] | None,
) -> set[str]:
    """Keep only approved tools backed by the trusted active-channel context."""

    if not approved_tool_names:
        return set()
    active_context = active_channel_context_from_trusted(trusted_runtime_context)
    if active_context is None:
        return set()
    allowed = set(channel_owned_tool_names_for_transport(active_context.transport))
    return {str(name).strip() for name in approved_tool_names if str(name).strip() in allowed}


def filter_generic_approved_tool_names(
    approved_tool_names: set[str] | tuple[str, ...] | None,
) -> set[str]:
    """Remove channel-owned grants from generic approval overrides."""

    if not approved_tool_names:
        return set()
    return {
        name
        for raw_name in approved_tool_names
        if (name := str(raw_name).strip()) and name not in RESERVED_CHANNEL_OWNED_TOOL_NAMES
    }


def _normalize_optional(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None
