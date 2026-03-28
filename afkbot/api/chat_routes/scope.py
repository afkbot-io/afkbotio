"""Scope and turn-context helpers shared by chat HTTP and WS routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException, status

from afkbot.api.chat_auth import (
    build_chat_auth_runtime_overrides,
    ensure_chat_target_scope,
    has_chat_auth_routing_snapshot,
    require_chat_http_context,
)
from afkbot.api.chat_targeting import build_chat_runtime_overrides, resolve_http_chat_target
from afkbot.services.connect import ConnectAccessTokenContext
from afkbot.services.agent_loop.turn_context import (
    TurnContextOverrides,
    merge_turn_context_overrides,
)
from afkbot.services.channel_routing.runtime_target import RuntimeTarget
from afkbot.settings import get_settings


@dataclass(frozen=True, slots=True)
class ResolvedHttpChatScope:
    """Authenticated chat scope resolved for one HTTP request."""

    auth_context: ConnectAccessTokenContext
    target: RuntimeTarget


async def resolve_http_chat_scope(
    *,
    authorization: str | None,
    session_proof: str | None,
    profile_id: str | None,
    session_id: str | None,
    resolve_binding: bool,
    require_binding_match: bool,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> ResolvedHttpChatScope:
    """Resolve authenticated HTTP chat scope and enforce token-bound session limits."""

    auth_context = await require_chat_http_context(
        authorization=authorization,
        session_proof=session_proof,
    )
    target = await resolve_http_chat_target(
        settings=get_settings(),
        profile_id=profile_id,
        session_id=session_id,
        resolve_binding=resolve_binding,
        require_binding_match=require_binding_match,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
        default_profile_id=auth_context.profile_id,
        default_session_id=auth_context.session_id,
    )
    ensure_chat_target_scope(
        context=auth_context,
        profile_id=target.profile_id,
        session_id=target.session_id,
    )
    return ResolvedHttpChatScope(auth_context=auth_context, target=target)


def build_resume_context_overrides(
    *,
    scope: ResolvedHttpChatScope,
    resolve_binding: bool,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
) -> TurnContextOverrides | None:
    """Build trusted routing overrides for resume-style requests."""

    runtime_overrides = None
    if not has_chat_auth_routing_snapshot(scope.auth_context) or resolve_binding:
        runtime_overrides = build_chat_runtime_overrides(
            target=scope.target,
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        )
    return merge_turn_context_overrides(
        build_chat_auth_runtime_overrides(scope.auth_context),
        runtime_overrides,
    )


def build_turn_context_overrides(
    *,
    scope: ResolvedHttpChatScope,
    resolve_binding: bool,
    transport: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
    plan_only: bool,
    planning_mode: Literal["off", "auto", "on"] | None,
    thinking_level: Literal["low", "medium", "high", "very_high"] | None,
) -> TurnContextOverrides | None:
    """Build runtime overrides for one chat turn request."""

    return merge_turn_context_overrides(
        build_resume_context_overrides(
            scope=scope,
            resolve_binding=resolve_binding,
            transport=transport,
            account_id=account_id,
            peer_id=peer_id,
            thread_id=thread_id,
            user_id=user_id,
        ),
        TurnContextOverrides(
            planning_mode="plan_only" if plan_only else "off",
            execution_planning_mode="off" if plan_only else planning_mode,
            thinking_level=thinking_level,
        ),
    )


def build_http_invalid_chat_request(reason: str) -> HTTPException:
    """Return deterministic 400 payload for invalid optional planning inputs."""

    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "ok": False,
            "error_code": "chat_request_invalid",
            "reason": reason,
        },
    )


__all__ = [
    "ResolvedHttpChatScope",
    "build_http_invalid_chat_request",
    "build_resume_context_overrides",
    "build_turn_context_overrides",
    "resolve_http_chat_scope",
]
