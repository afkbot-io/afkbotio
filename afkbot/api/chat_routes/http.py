"""HTTP route handlers for chat API endpoints."""

from __future__ import annotations

from fastapi import Header, Query

from afkbot.api.chat_routes.contracts import (
    ChatTurnRequest,
    QuestionAnswerRequest,
    SecureFieldSubmitRequest,
    SecureFieldSubmitResponse,
)
from afkbot.api.chat_routes.scope import (
    build_http_invalid_chat_request,
    build_resume_context_overrides,
    build_turn_context_overrides,
    resolve_http_chat_scope,
)
from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.api_runtime import (
    ProgressPollResponse,
    poll_chat_progress,
    resolve_pending_question_envelope,
    resume_chat_after_secure_submit,
    resume_chat_interaction,
    run_chat_turn,
)
from afkbot.services.agent_loop.progress_stream import ProgressCursor
from afkbot.services.agent_loop.turn_runtime import submit_secure_field
from afkbot.services.app_catalog import AppCatalogResponse, get_app_catalog_service
from afkbot.services.llm.reasoning import normalize_thinking_level
from afkbot.settings import get_settings


async def post_chat_turn(
    request: ChatTurnRequest,
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
) -> TurnResult:
    """Execute one chat turn via AgentLoop and return deterministic envelope."""

    scope = await resolve_http_chat_scope(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=request.profile_id,
        session_id=request.session_id,
        resolve_binding=request.resolve_binding,
        require_binding_match=request.require_binding_match,
        transport=request.transport,
        account_id=request.account_id,
        peer_id=request.peer_id,
        thread_id=request.thread_id,
        user_id=request.user_id,
    )
    try:
        thinking_level = normalize_thinking_level(request.thinking_level)
    except ValueError as exc:
        raise build_http_invalid_chat_request(str(exc)) from exc
    turn_overrides = build_turn_context_overrides(
        scope=scope,
        resolve_binding=request.resolve_binding,
        transport=request.transport,
        account_id=request.account_id,
        peer_id=request.peer_id,
        thread_id=request.thread_id,
        user_id=request.user_id,
        plan_only=request.plan_only,
        planning_mode=request.planning_mode,
        thinking_level=thinking_level,
    )
    return await run_chat_turn(
        message=request.message,
        profile_id=scope.target.profile_id,
        session_id=scope.target.session_id,
        client_msg_id=request.client_msg_id,
        context_overrides=turn_overrides,
    )


async def get_chat_catalog(
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
    profile_id: str | None = Query(default=None, min_length=1),
    session_id: str | None = Query(default=None, min_length=1),
    resolve_binding: bool = Query(default=False),
    require_binding_match: bool = Query(default=False),
    transport: str | None = Query(default=None, min_length=1),
    account_id: str | None = Query(default=None, min_length=1),
    peer_id: str | None = Query(default=None, min_length=1),
    thread_id: str | None = Query(default=None, min_length=1),
    user_id: str | None = Query(default=None, min_length=1),
) -> AppCatalogResponse:
    """Return normalized runtime metadata and mention catalog for the active scope."""

    scope = await resolve_http_chat_scope(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=profile_id,
        session_id=session_id,
        resolve_binding=resolve_binding,
        require_binding_match=require_binding_match,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    return await get_app_catalog_service(get_settings()).get_catalog(
        profile_id=scope.target.profile_id,
        session_id=scope.target.session_id,
    )


async def get_chat_progress(
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
    profile_id: str | None = Query(default=None, min_length=1),
    session_id: str | None = Query(default=None, min_length=1),
    resolve_binding: bool = Query(default=False),
    require_binding_match: bool = Query(default=False),
    transport: str | None = Query(default=None, min_length=1),
    account_id: str | None = Query(default=None, min_length=1),
    peer_id: str | None = Query(default=None, min_length=1),
    thread_id: str | None = Query(default=None, min_length=1),
    user_id: str | None = Query(default=None, min_length=1),
    run_id: int | None = Query(default=None, ge=1),
    after_event_id: int = Query(default=0, ge=0),
) -> ProgressPollResponse:
    """Poll canonical progress events for one profile/session pair."""

    scope = await resolve_http_chat_scope(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=profile_id,
        session_id=session_id,
        resolve_binding=resolve_binding,
        require_binding_match=require_binding_match,
        transport=transport,
        account_id=account_id,
        peer_id=peer_id,
        thread_id=thread_id,
        user_id=user_id,
    )
    return await poll_chat_progress(
        profile_id=scope.target.profile_id,
        session_id=scope.target.session_id,
        cursor=ProgressCursor(run_id=run_id, last_event_id=after_event_id),
    )


async def post_secure_field(
    request: SecureFieldSubmitRequest,
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
) -> SecureFieldSubmitResponse:
    """Store secure credential value requested by `request_secure_field` envelope."""

    scope = await resolve_http_chat_scope(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=request.profile_id,
        session_id=request.session_id,
        resolve_binding=request.resolve_binding,
        require_binding_match=request.require_binding_match,
        transport=request.transport,
        account_id=request.account_id,
        peer_id=request.peer_id,
        thread_id=request.thread_id,
        user_id=request.user_id,
    )
    envelope = await resolve_pending_question_envelope(
        profile_id=scope.target.profile_id,
        session_id=scope.target.session_id,
        question_id=request.question_id,
        action="request_secure_field",
        secure_field=request.secure_field,
    )
    if envelope is None:
        return SecureFieldSubmitResponse(ok=False, error_code="pending_secure_request_not_found")

    ok, code = await submit_secure_field(
        profile_id=scope.target.profile_id,
        envelope=envelope,
        secret_value=request.secret_value,
        session_id=scope.target.session_id,
    )
    if not ok or not request.resume_after_submit:
        return SecureFieldSubmitResponse(ok=ok, error_code=code)

    next_turn = await resume_chat_after_secure_submit(
        envelope=envelope,
        profile_id=scope.target.profile_id,
        session_id=scope.target.session_id,
        client_msg_id=request.client_msg_id,
        context_overrides=build_resume_context_overrides(
            scope=scope,
            resolve_binding=request.resolve_binding,
            transport=request.transport,
            account_id=request.account_id,
            peer_id=request.peer_id,
            thread_id=request.thread_id,
            user_id=request.user_id,
        ),
    )
    return SecureFieldSubmitResponse(ok=ok, error_code=code, next_turn=next_turn)


async def post_question_answer(
    request: QuestionAnswerRequest,
    authorization: str | None = Header(default=None),
    x_afk_session_proof: str | None = Header(default=None),
) -> TurnResult:
    """Resume one ask-question interaction from app approval or profile-selection UI."""

    scope = await resolve_http_chat_scope(
        authorization=authorization,
        session_proof=x_afk_session_proof,
        profile_id=request.profile_id,
        session_id=request.session_id,
        resolve_binding=request.resolve_binding,
        require_binding_match=request.require_binding_match,
        transport=request.transport,
        account_id=request.account_id,
        peer_id=request.peer_id,
        thread_id=request.thread_id,
        user_id=request.user_id,
    )
    envelope = await resolve_pending_question_envelope(
        profile_id=scope.target.profile_id,
        session_id=scope.target.session_id,
        question_id=request.question_id,
        action="ask_question",
    )
    if envelope is None:
        raise build_http_invalid_chat_request("Pending question not found for provided question_id")

    return await resume_chat_interaction(
        envelope=envelope,
        profile_id=scope.target.profile_id,
        session_id=scope.target.session_id,
        approved=request.approved,
        answer_text=request.answer,
        client_msg_id=request.client_msg_id,
        context_overrides=build_resume_context_overrides(
            scope=scope,
            resolve_binding=request.resolve_binding,
            transport=request.transport,
            account_id=request.account_id,
            peer_id=request.peer_id,
            thread_id=request.thread_id,
            user_id=request.user_id,
        ),
    )


__all__ = [
    "get_chat_catalog",
    "get_chat_progress",
    "post_chat_turn",
    "post_question_answer",
    "post_secure_field",
]
