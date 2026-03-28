"""Target resolution helpers for connect CLI."""

from __future__ import annotations

import asyncio

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.channel_routing import (
    ChannelBindingServiceError,
    RoutingSelectors,
    RuntimeTarget,
    build_routing_context_overrides,
    resolve_runtime_target,
)
from afkbot.settings import Settings


def resolve_cli_connect_target(
    *,
    settings: Settings,
    profile_id: str,
    session_id: str,
    resolve_binding: bool,
    require_binding_match: bool,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> RuntimeTarget:
    """Resolve effective profile/session for connect URL issuance."""

    if resolve_binding and not (transport or "").strip():
        raise_usage_error("--transport is required with --resolve-binding")
    selectors = RoutingSelectors(
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    try:
        return asyncio.run(
            resolve_runtime_target(
                settings=settings,
                explicit_profile_id=profile_id,
                explicit_session_id=session_id,
                resolve_binding=resolve_binding,
                require_binding_match=require_binding_match,
                selectors=selectors,
                default_profile_id="default",
                default_session_id="desktop-session",
            )
        )
    except (ChannelBindingServiceError, ValueError) as exc:
        raise_usage_error(str(exc))


def build_cli_connect_runtime_overrides(
    *,
    target: RuntimeTarget,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> TurnContextOverrides | None:
    """Build trusted routing snapshot persisted into connect session tokens."""

    return build_routing_context_overrides(
        target=target,
        selectors=RoutingSelectors(
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        ),
    )
