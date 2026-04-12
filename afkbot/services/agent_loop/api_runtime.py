"""API-facing lifecycle and turn-execution wrappers over AgentLoop services."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.services.browser_sessions import get_browser_session_manager
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, ActionType, TurnResult
from afkbot.services.agent_loop.api_runtime_resume import (
    resume_chat_after_secure_submit_flow,
    resume_chat_interaction_flow,
)
from afkbot.services.agent_loop.api_runtime_support import (
    ProgressPollResponse,
    dispose_owned_engine,
    heartbeat_turn_claim,
    idempotency_claim_cutoff as idempotency_claim_cutoff_support,
    resolve_session_resources,
    resolve_trusted_pending_envelope as resolve_trusted_pending_envelope_support,
    wait_for_claimed_turn_result,
)
from afkbot.services.agent_loop.api_runtime_turns import (
    run_idempotent_chat_turn,
)
from afkbot.services.agent_loop.progress_stream import (
    ProgressCursor,
    ProgressEvent,
    ProgressStream,
)
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.agent_loop.turn_runtime import run_once_result
from afkbot.services.tools.base import ToolCall
from afkbot.settings import Settings, get_settings

__all__ = [
    "ProgressPollResponse",
    "get_api_session_factory",
    "initialize_api_runtime",
    "poll_chat_progress",
    "resolve_pending_question_envelope",
    "resume_chat_after_secure_submit",
    "resume_chat_interaction",
    "run_chat_turn",
    "shutdown_api_runtime",
]


@dataclass(slots=True, frozen=True)
class _ApiRuntimeState:
    """Shared DB resources for API routes lifecycle."""

    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]


_API_RUNTIME_STATE: _ApiRuntimeState | None = None
_API_RUNTIME_LOCK = asyncio.Lock()
_IDEMPOTENCY_HEARTBEAT_SEC = 15.0


async def initialize_api_runtime(*, settings: Settings | None = None) -> None:
    """Initialize shared API runtime DB resources for FastAPI lifespan."""

    global _API_RUNTIME_STATE
    if _API_RUNTIME_STATE is not None:
        return

    async with _API_RUNTIME_LOCK:
        if _API_RUNTIME_STATE is not None:
            return
        resolved_settings = settings or get_settings()
        engine = create_engine(resolved_settings)
        await create_schema(engine)
        _API_RUNTIME_STATE = _ApiRuntimeState(
            engine=engine,
            session_factory=create_session_factory(engine),
        )


async def shutdown_api_runtime() -> None:
    """Dispose shared API runtime resources if they were initialized."""

    global _API_RUNTIME_STATE
    settings = get_settings()
    async with _API_RUNTIME_LOCK:
        state = _API_RUNTIME_STATE
        _API_RUNTIME_STATE = None
    if state is not None:
        await get_browser_session_manager().close_all_for_root(root_dir=settings.root_dir)
        await state.engine.dispose()


def get_api_session_factory() -> async_sessionmaker[AsyncSession] | None:
    """Return the initialized shared session factory for API runtime, if any."""

    state = _API_RUNTIME_STATE
    if state is None:
        return None
    return state.session_factory


async def _wait_for_claimed_turn_result(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    profile_id: str,
    session_id: str,
    client_msg_id: str,
) -> TurnResult | None:
    """Wait for the in-flight owner to persist a deterministic turn result."""

    return await wait_for_claimed_turn_result(
        session_factory=session_factory,
        settings=settings,
        profile_id=profile_id,
        session_id=session_id,
        client_msg_id=client_msg_id,
        claim_cutoff_factory=idempotency_claim_cutoff_support,
    )


async def _heartbeat_turn_claim(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
    session_id: str,
    client_msg_id: str,
    owner_token: str,
) -> None:
    """Refresh one in-flight claim until turn execution completes."""

    await heartbeat_turn_claim(
        session_factory=session_factory,
        profile_id=profile_id,
        session_id=session_id,
        client_msg_id=client_msg_id,
        owner_token=owner_token,
        heartbeat_sec=_IDEMPOTENCY_HEARTBEAT_SEC,
    )


async def run_chat_turn(
    *,
    message: str,
    profile_id: str,
    session_id: str,
    client_msg_id: str | None = None,
    planned_tool_calls: list[ToolCall] | None = None,
    context_overrides: TurnContextOverrides | None = None,
) -> TurnResult:
    """Execute one chat turn via the canonical runtime service."""

    settings = get_settings()
    session_factory = get_api_session_factory()

    async def _execute_api_turn(
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls: list[ToolCall] | None = None,
        progress_sink: Callable[[ProgressEvent], None] | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult:
        return await run_once_result(
            message=message,
            profile_id=profile_id,
            session_id=session_id,
            settings=settings,
            session_factory=session_factory,
            planned_tool_calls=planned_tool_calls,
            progress_sink=progress_sink,
            context_overrides=context_overrides,
            source="api",
        )

    normalized_client_msg_id = (client_msg_id or "").strip()
    if normalized_client_msg_id:
        return await run_idempotent_chat_turn(
            message=message,
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=normalized_client_msg_id,
            planned_tool_calls=planned_tool_calls,
            context_overrides=context_overrides,
            settings=settings,
            shared_session_factory=session_factory,
            execute_turn=_execute_api_turn,
            wait_for_claimed_turn_result=_wait_for_claimed_turn_result,
            heartbeat_turn_claim=_heartbeat_turn_claim,
        )

    return await _execute_api_turn(
        message=message,
        profile_id=profile_id,
        session_id=session_id,
        planned_tool_calls=planned_tool_calls,
        progress_sink=None,
        context_overrides=context_overrides,
    )


async def resume_chat_interaction(
    *,
    envelope: ActionEnvelope,
    profile_id: str,
    session_id: str,
    approved: bool | None = None,
    answer_text: str | None = None,
    client_msg_id: str | None = None,
    context_overrides: TurnContextOverrides | None = None,
) -> TurnResult:
    """Resume one ask-question flow through canonical API runtime adapters."""

    return await resume_chat_interaction_flow(
        envelope=envelope,
        profile_id=profile_id,
        session_id=session_id,
        approved=approved,
        answer_text=answer_text,
        client_msg_id=client_msg_id,
        context_overrides=context_overrides,
        resolve_pending_envelope=resolve_pending_question_envelope,
        run_chat_turn_call=run_chat_turn,
    )


async def resume_chat_after_secure_submit(
    *,
    envelope: ActionEnvelope,
    profile_id: str,
    session_id: str,
    client_msg_id: str | None = None,
    context_overrides: TurnContextOverrides | None = None,
) -> TurnResult:
    """Resume one secure interaction after secret capture without replaying raw user text."""

    return await resume_chat_after_secure_submit_flow(
        envelope=envelope,
        profile_id=profile_id,
        session_id=session_id,
        client_msg_id=client_msg_id,
        context_overrides=context_overrides,
        resolve_pending_envelope=resolve_pending_question_envelope,
        run_chat_turn_call=run_chat_turn,
    )


async def poll_chat_progress(
    *,
    profile_id: str,
    session_id: str,
    cursor: ProgressCursor,
) -> ProgressPollResponse:
    """Poll progress events for one profile/session pair and return the next cursor."""

    resources = await resolve_session_resources(shared_session_factory=get_api_session_factory())
    try:
        async with session_scope(resources.session_factory) as db:
            stream = ProgressStream(db, batch_size=get_settings().cli_progress_batch_size)
            events, next_cursor = await stream.poll(
                profile_id=profile_id,
                session_id=session_id,
                cursor=cursor,
            )
            return ProgressPollResponse(events=events, cursor=next_cursor)
    finally:
        await dispose_owned_engine(resources)


async def resolve_pending_question_envelope(
    *,
    profile_id: str,
    session_id: str,
    question_id: str | None,
    action: ActionType,
    secure_field: str | None = None,
) -> ActionEnvelope | None:
    """Resolve one trusted pending envelope by profile, session, and question identifiers."""

    return await resolve_trusted_pending_envelope_support(
        shared_session_factory=get_api_session_factory(),
        profile_id=profile_id,
        session_id=session_id,
        question_id=question_id,
        action=action,
        secure_field=secure_field,
    )
