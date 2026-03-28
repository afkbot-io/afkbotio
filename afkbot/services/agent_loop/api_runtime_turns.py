"""Idempotent turn-execution helpers for API-facing AgentLoop runtime."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.chat_turn_idempotency_repo import ChatTurnIdempotencyRepository
from afkbot.services.agent_loop.action_contracts import TurnResult
from afkbot.services.agent_loop.api_runtime_support import (
    dispose_owned_engine,
    idempotency_row_to_turn_result,
    resolve_session_resources,
)
from afkbot.services.agent_loop.progress_stream import ProgressEvent
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.tools.base import ToolCall
from afkbot.settings import Settings


class TurnExecutor(Protocol):
    """Execute one agent-loop turn and return the persisted result envelope."""

    async def __call__(
        self,
        *,
        message: str,
        profile_id: str,
        session_id: str,
        planned_tool_calls: list[ToolCall] | None = None,
        progress_sink: Callable[[ProgressEvent], None] | None = None,
        context_overrides: TurnContextOverrides | None = None,
    ) -> TurnResult: ...


class ClaimedTurnWaiter(Protocol):
    """Wait for another worker to finish a previously claimed turn."""

    async def __call__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        profile_id: str,
        session_id: str,
        client_msg_id: str,
    ) -> TurnResult | None: ...


class ClaimHeartbeater(Protocol):
    """Refresh one claimed idempotency row while a turn is still executing."""

    async def __call__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        profile_id: str,
        session_id: str,
        client_msg_id: str,
        owner_token: str,
    ) -> None: ...


async def _execute_turn_once(
    *,
    execute_turn: TurnExecutor,
    message: str,
    profile_id: str,
    session_id: str,
    planned_tool_calls: list[ToolCall] | None,
    context_overrides: TurnContextOverrides | None,
) -> TurnResult:
    """Run one turn while preserving the legacy optional context-overrides call shape."""

    if context_overrides is None:
        return await execute_turn(
            message=message,
            profile_id=profile_id,
            session_id=session_id,
            planned_tool_calls=planned_tool_calls,
            progress_sink=None,
        )
    return await execute_turn(
        message=message,
        profile_id=profile_id,
        session_id=session_id,
        planned_tool_calls=planned_tool_calls,
        progress_sink=None,
        context_overrides=context_overrides,
    )


async def _claim_or_load_existing_result(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
    session_id: str,
    client_msg_id: str,
    owner_token: str,
) -> tuple[bool, TurnResult | None]:
    """Try to claim one idempotent turn or load an already persisted result."""

    async with session_scope(session_factory) as db:
        repo = ChatTurnIdempotencyRepository(db)
        existing = await repo.get_by_key(
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
        )
        if existing is not None:
            return False, idempotency_row_to_turn_result(existing)
        claimed = await repo.try_claim(
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
            owner_token=owner_token,
        )
    return claimed, None


async def _store_or_reuse_result(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
    session_id: str,
    client_msg_id: str,
    result: TurnResult,
) -> TurnResult:
    """Persist one deterministic result or reuse an already stored sibling result."""

    async with session_scope(session_factory) as db:
        repo = ChatTurnIdempotencyRepository(db)
        created = await repo.create(
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
            run_id=result.run_id,
            envelope_json=json.dumps(
                result.envelope.model_dump(mode="json"),
                ensure_ascii=True,
                sort_keys=True,
            ),
        )
        if created is not None:
            return result
        existing = await repo.get_by_key(
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
        )
        if existing is not None:
            return idempotency_row_to_turn_result(existing)
        return result


async def _release_claim(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    profile_id: str,
    session_id: str,
    client_msg_id: str,
    owner_token: str,
) -> None:
    """Release one owner claim once execution completes or aborts."""

    async with session_scope(session_factory) as db:
        await ChatTurnIdempotencyRepository(db).release_claim(
            profile_id=profile_id,
            session_id=session_id,
            client_msg_id=client_msg_id,
            owner_token=owner_token,
        )


async def run_idempotent_chat_turn(
    *,
    message: str,
    profile_id: str,
    session_id: str,
    client_msg_id: str,
    planned_tool_calls: list[ToolCall] | None,
    context_overrides: TurnContextOverrides | None,
    settings: Settings,
    shared_session_factory: async_sessionmaker[AsyncSession] | None,
    execute_turn: TurnExecutor,
    wait_for_claimed_turn_result: ClaimedTurnWaiter,
    heartbeat_turn_claim: ClaimHeartbeater,
) -> TurnResult:
    """Run one chat turn behind an idempotent claim-and-persist workflow."""

    resources = await resolve_session_resources(
        shared_session_factory=shared_session_factory,
        settings=settings,
    )
    claim_owner_token = uuid.uuid4().hex
    claim_heartbeat_task: asyncio.Task[None] | None = None
    try:
        while True:
            claimed, existing_result = await _claim_or_load_existing_result(
                session_factory=resources.session_factory,
                profile_id=profile_id,
                session_id=session_id,
                client_msg_id=client_msg_id,
                owner_token=claim_owner_token,
            )
            if existing_result is not None:
                return existing_result
            if claimed:
                break

            waited = await wait_for_claimed_turn_result(
                session_factory=resources.session_factory,
                settings=settings,
                profile_id=profile_id,
                session_id=session_id,
                client_msg_id=client_msg_id,
            )
            if waited is not None:
                return waited

        try:
            claim_heartbeat_task = asyncio.create_task(
                heartbeat_turn_claim(
                    session_factory=resources.session_factory,
                    profile_id=profile_id,
                    session_id=session_id,
                    client_msg_id=client_msg_id,
                    owner_token=claim_owner_token,
                ),
                name=f"chat-turn-claim-heartbeat:{profile_id}:{session_id}:{client_msg_id}",
            )
            result = await _execute_turn_once(
                execute_turn=execute_turn,
                message=message,
                profile_id=profile_id,
                session_id=session_id,
                planned_tool_calls=planned_tool_calls,
                context_overrides=context_overrides,
            )
            return await _store_or_reuse_result(
                session_factory=resources.session_factory,
                profile_id=profile_id,
                session_id=session_id,
                client_msg_id=client_msg_id,
                result=result,
            )
        finally:
            if claim_heartbeat_task is not None:
                claim_heartbeat_task.cancel()
                try:
                    await claim_heartbeat_task
                except asyncio.CancelledError:
                    pass
            await _release_claim(
                session_factory=resources.session_factory,
                profile_id=profile_id,
                session_id=session_id,
                client_msg_id=client_msg_id,
                owner_token=claim_owner_token,
            )
    finally:
        await dispose_owned_engine(resources)
