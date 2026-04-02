"""Target resolution helpers for chat CLI."""

from __future__ import annotations

import asyncio

from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.agent_loop.turn_context import (
    TurnContextOverrides,
    merge_turn_context_overrides,
)
from afkbot.services.channel_routing import (
    ChannelBindingServiceError,
    RoutingSelectors,
    RuntimeTarget,
    build_routing_context_overrides,
    resolve_runtime_target,
)
from afkbot.settings import Settings


def resolve_cli_chat_target(
    *,
    settings: Settings,
    profile_id: str,
    session_id: str | None,
    resolve_binding: bool,
    require_binding_match: bool,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> RuntimeTarget:
    """Resolve effective profile/session for CLI chat mode."""

    if resolve_binding and not (transport or "").strip():
        raise_usage_error("--transport is required with --resolve-binding")
    fallback_session_id = _default_cli_session_id(profile_id=profile_id)
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
                default_profile_id=profile_id,
                default_session_id=fallback_session_id,
            )
        )
    except (ChannelBindingServiceError, ValueError) as exc:
        raise_usage_error(str(exc))


def _default_cli_session_id(*, profile_id: str) -> str:
    normalized = profile_id.strip() or "default"
    return f"cli:{normalized}"


def build_cli_runtime_overrides(
    *,
    target: RuntimeTarget,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> TurnContextOverrides | None:
    """Build turn context overrides from resolved routing target."""

    routing_overrides = build_routing_context_overrides(
        target=target,
        selectors=RoutingSelectors(
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        ),
    )
    runtime_metadata = dict(
        {}
        if routing_overrides is None or not routing_overrides.runtime_metadata
        else routing_overrides.runtime_metadata
    )
    if not str(runtime_metadata.get("transport") or "").strip():
        runtime_metadata["transport"] = "cli"
    cli_overrides = TurnContextOverrides(
        runtime_metadata=runtime_metadata,
        cli_approval_surface_enabled=True,
        prompt_overlay=(
            "In afk chat, some visible tools may require explicit user approval before execution. "
            "If a suitable tool is available, you may propose and call it; the runtime will request "
            "confirmation when needed. Do not claim that a visible approval-gated tool is unavailable "
            "just because it needs confirmation."
        ),
    )
    return merge_turn_context_overrides(routing_overrides, cli_overrides)
