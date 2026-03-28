"""Binding-aware target resolution helpers for chat HTTP/WS adapters."""

from __future__ import annotations

from fastapi import HTTPException, status

from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.channel_routing import (
    ChannelBindingServiceError,
    RoutingSelectors,
    RuntimeTarget,
    build_routing_context_overrides,
    resolve_runtime_target,
)
from afkbot.settings import Settings


async def resolve_http_chat_target(
    *,
    settings: Settings,
    profile_id: str | None,
    session_id: str | None,
    resolve_binding: bool,
    require_binding_match: bool,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
    default_profile_id: str,
    default_session_id: str,
) -> RuntimeTarget:
    """Resolve effective profile/session or raise deterministic HTTP 400 payload."""

    try:
        return await resolve_runtime_target(
            settings=settings,
            explicit_profile_id=profile_id,
            explicit_session_id=session_id,
            resolve_binding=resolve_binding,
            require_binding_match=require_binding_match,
            selectors=_build_routing_selectors(
                transport=transport,
                account_id=account_id,
                peer_id=peer_id,
                thread_id=thread_id,
                user_id=user_id,
            ),
            default_profile_id=default_profile_id,
            default_session_id=default_session_id,
        )
    except (ChannelBindingServiceError, ValueError) as exc:
        raise chat_request_http_error(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=getattr(exc, "error_code", "chat_request_invalid"),
            reason=str(exc),
        ) from exc


async def build_ws_chat_target_or_error(
    *,
    settings: Settings,
    profile_id: str | None,
    session_id: str | None,
    resolve_binding: bool,
    require_binding_match: bool,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
    default_profile_id: str,
    default_session_id: str,
) -> tuple[RuntimeTarget | None, dict[str, object] | None]:
    """Resolve WS target or return deterministic error payload."""

    try:
        target = await resolve_runtime_target(
            settings=settings,
            explicit_profile_id=profile_id,
            explicit_session_id=session_id,
            resolve_binding=resolve_binding,
            require_binding_match=require_binding_match,
            selectors=_build_routing_selectors(
                transport=transport,
                account_id=account_id,
                peer_id=peer_id,
                thread_id=thread_id,
                user_id=user_id,
            ),
            default_profile_id=default_profile_id,
            default_session_id=default_session_id,
        )
    except (ChannelBindingServiceError, ValueError) as exc:
        return (
            None,
            chat_request_payload(
                error_code=getattr(exc, "error_code", "chat_request_invalid"),
                reason=str(exc),
            ),
        )
    return target, None


def chat_request_payload(*, error_code: str, reason: str) -> dict[str, object]:
    """Build deterministic non-auth request error payload."""

    return {
        "ok": False,
        "error_code": error_code,
        "reason": reason,
    }


def chat_request_http_error(*, status_code: int, error_code: str, reason: str) -> HTTPException:
    """Build deterministic HTTPException for request-shape/routing failures."""

    return HTTPException(
        status_code=status_code,
        detail=chat_request_payload(error_code=error_code, reason=reason),
    )


def build_chat_runtime_overrides(
    *,
    target: RuntimeTarget,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> TurnContextOverrides | None:
    """Build turn context overrides for HTTP/WS chat turns."""

    return build_routing_context_overrides(
        target=target,
        selectors=_build_routing_selectors(
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        ),
    )


def _build_routing_selectors(
    *,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> RoutingSelectors:
    """Build one shared selector payload for HTTP and WS chat routing."""

    return RoutingSelectors(
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
