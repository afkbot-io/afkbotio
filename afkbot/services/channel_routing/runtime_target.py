"""Runtime target resolution over explicit profile/session and optional channel bindings."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.agent_loop.thinking import combine_prompt_overlays
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.channel_routing.contracts import (
    ChannelRoutingDecision,
    ChannelRoutingInput,
    ChannelRoutingTelemetryEvent,
)
from afkbot.services.channel_routing.policy import (
    is_user_facing_transport,
    normalize_transport_name,
    requires_strict_binding_match,
)
from afkbot.services.channel_routing.service import ChannelBindingServiceError, get_channel_binding_service
from afkbot.services.profile_id import validate_profile_id
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class RoutingSelectors:
    """Normalized transport selectors carried by one ingress request."""

    transport: str | None = None
    account_id: str | None = None
    peer_id: str | None = None
    thread_id: str | None = None
    user_id: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeTarget:
    """Effective profile/session target for one ingress request."""

    profile_id: str
    session_id: str
    routing: ChannelRoutingDecision | None = None


_PUBLIC_CHANNEL_PROMPT_OVERLAY = """# External Channel Behavior
You are replying in a user-facing external channel.

Requirements:
- present yourself according to the active profile role, not as a generic tool host;
- do not proactively enumerate internal tools, plugin names, runtime internals, or raw infrastructure capabilities;
- when asked what you can do, describe only role-appropriate help in user-facing terms;
- do not claim shell, filesystem, HTTP, web search, browser, automation, or app-integration abilities unless they are both available and appropriate for the active profile;
- if the answer depends on profile-local docs or trusted project files, prefer those sources and say when you do not know.
"""


async def resolve_runtime_target(
    *,
    settings: Settings,
    explicit_profile_id: str | None,
    explicit_session_id: str | None,
    resolve_binding: bool,
    require_binding_match: bool = False,
    selectors: RoutingSelectors | None = None,
    transport: str | None = None,
    account_id: str | None = None,
    peer_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
    default_profile_id: str,
    default_session_id: str,
) -> RuntimeTarget:
    """Resolve effective runtime target with explicit values overridden by matching binding."""

    fallback_profile_id = validate_profile_id((explicit_profile_id or "").strip() or default_profile_id)
    fallback_session_id = (explicit_session_id or "").strip() or default_session_id
    routing_selectors = build_routing_selectors(
        selectors=selectors,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    if not resolve_binding:
        return RuntimeTarget(profile_id=fallback_profile_id, session_id=fallback_session_id)

    normalized_transport = normalize_transport_name(routing_selectors.transport)
    if not normalized_transport:
        raise ChannelBindingServiceError(
            error_code="channel_binding_transport_required",
            reason="Transport is required when binding resolution is enabled.",
        )
    effective_require_binding_match = requires_strict_binding_match(
        settings=settings,
        transport=normalized_transport,
        require_binding_match=require_binding_match,
    )

    service = get_channel_binding_service(settings)
    decision = await service.resolve(
        routing_input=ChannelRoutingInput(
            transport=normalized_transport,
            account_id=routing_selectors.account_id,
            peer_id=routing_selectors.peer_id,
            thread_id=routing_selectors.thread_id,
            user_id=routing_selectors.user_id,
            default_session_id=fallback_session_id,
        )
    )
    if decision is None:
        await service.record_outcome(
            event=_build_routing_telemetry_event(
                selectors=routing_selectors,
                decision=None,
                strict=effective_require_binding_match,
                fallback_used=not effective_require_binding_match,
            )
        )
        if effective_require_binding_match:
            raise ChannelBindingServiceError(
                error_code="channel_binding_no_match",
                reason="No channel binding matched the provided target selectors.",
            )
        return RuntimeTarget(profile_id=fallback_profile_id, session_id=fallback_session_id)
    await service.record_outcome(
        event=_build_routing_telemetry_event(
            selectors=routing_selectors,
            decision=decision,
            strict=effective_require_binding_match,
            fallback_used=False,
        )
    )
    return RuntimeTarget(
        profile_id=decision.profile_id,
        session_id=decision.session_id,
        routing=decision,
    )


def build_routing_runtime_metadata(
    *,
    target: RuntimeTarget,
    selectors: RoutingSelectors | None = None,
    transport: str | None = None,
    account_id: str | None = None,
    peer_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, object] | None:
    """Project routing target and ingress selectors into runtime metadata."""

    routing_selectors = build_routing_selectors(
        selectors=selectors,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    if target.routing is None and not any(
        (
            routing_selectors.transport,
            routing_selectors.account_id,
            routing_selectors.peer_id,
            routing_selectors.thread_id,
            routing_selectors.user_id,
        )
    ):
        return None
    payload: dict[str, object] = {
        "transport": routing_selectors.transport,
        "account_id": routing_selectors.account_id,
        "peer_id": routing_selectors.peer_id,
        "thread_id": routing_selectors.thread_id,
        "user_id": routing_selectors.user_id,
    }
    if target.routing is not None:
        payload["channel_binding"] = {
            "binding_id": target.routing.binding_id,
            "session_policy": target.routing.session_policy,
        }
    return {key: value for key, value in payload.items() if value is not None}


def build_routing_context_overrides(
    *,
    target: RuntimeTarget,
    selectors: RoutingSelectors | None = None,
    transport: str | None = None,
    account_id: str | None = None,
    peer_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> TurnContextOverrides | None:
    """Build turn-scoped routing metadata and prompt overlay overrides."""

    routing_selectors = build_routing_selectors(
        selectors=selectors,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    runtime_metadata = build_routing_runtime_metadata(
        target=target,
        selectors=routing_selectors,
    )
    prompt_overlay = combine_prompt_overlays(
        _default_public_channel_prompt_overlay(routing_selectors.transport),
        None if target.routing is None else target.routing.prompt_overlay,
    )
    if runtime_metadata is None and not prompt_overlay:
        return None
    return TurnContextOverrides(
        runtime_metadata=runtime_metadata,
        prompt_overlay=prompt_overlay,
    )


def build_routing_selectors(
    *,
    selectors: RoutingSelectors | None = None,
    transport: str | None = None,
    account_id: str | None = None,
    peer_id: str | None = None,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> RoutingSelectors:
    """Normalize ingress selector values into one reusable record."""

    if selectors is not None:
        return RoutingSelectors(
            transport=normalize_transport_name(selectors.transport),
            account_id=(selectors.account_id or "").strip() or None,
            peer_id=(selectors.peer_id or "").strip() or None,
            thread_id=(selectors.thread_id or "").strip() or None,
            user_id=(selectors.user_id or "").strip() or None,
        )
    return RoutingSelectors(
        transport=normalize_transport_name(transport),
        account_id=(account_id or "").strip() or None,
        peer_id=(peer_id or "").strip() or None,
        thread_id=(thread_id or "").strip() or None,
        user_id=(user_id or "").strip() or None,
    )


def _build_routing_telemetry_event(
    *,
    selectors: RoutingSelectors,
    decision: ChannelRoutingDecision | None,
    strict: bool,
    fallback_used: bool,
) -> ChannelRoutingTelemetryEvent:
    """Build one structured routing telemetry event from final resolution outcome."""

    return ChannelRoutingTelemetryEvent(
        transport=selectors.transport or "unknown",
        account_id=selectors.account_id,
        peer_id=selectors.peer_id,
        thread_id=selectors.thread_id,
        user_id=selectors.user_id,
        strict=strict,
        matched=decision is not None,
        no_match=decision is None,
        fallback_used=fallback_used,
        binding_id=None if decision is None else decision.binding_id,
        profile_id=None if decision is None else decision.profile_id,
        session_policy=None if decision is None else decision.session_policy,
        prompt_overlay_applied=bool(None if decision is None else decision.prompt_overlay),
    )


def _default_public_channel_prompt_overlay(transport: str | None) -> str | None:
    """Return trusted user-facing overlay for external channel transports."""

    normalized_transport = normalize_transport_name(transport)
    if not is_user_facing_transport(normalized_transport):
        return None
    return _PUBLIC_CHANNEL_PROMPT_OVERLAY
