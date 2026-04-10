"""Tests for canonical session turn orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.session import session_scope
from afkbot.models.chat_session_turn_queue import ChatSessionTurnQueueItem
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.session_orchestration import SessionOrchestrator
from tests.services.agent_loop._loop_harness import create_test_db


class _ObservedRunner:
    def __init__(
        self,
        session: AsyncSession,
        *,
        started: list[str],
        first_started: asyncio.Event,
        release_first: asyncio.Event,
    ) -> None:
        self._session = session
        self._started = started
        self._first_started = first_started
        self._release_first = release_first

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        **_unused: object,
    ) -> TurnResult:
        self._started.append(message)
        sessions = ChatSessionRepository(self._session)
        if await sessions.get(session_id) is None:
            await sessions.create(session_id=session_id, profile_id=profile_id)
        if message == "first":
            self._first_started.set()
            await self._release_first.wait()
        run = await RunRepository(self._session).create_run(
            session_id=session_id,
            profile_id=profile_id,
            status="completed",
        )
        return TurnResult(
            run_id=run.id,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message=f"done:{message}"),
        )


@pytest.mark.asyncio
async def test_session_orchestrator_serializes_different_messages_for_same_session(
    tmp_path: Path,
) -> None:
    settings, engine, factory = await create_test_db(tmp_path, "session_orchestrator.db")
    started: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="s-queue", profile_id="default")

    orchestrator = SessionOrchestrator(
        settings=settings,
        session_factory=factory,
        turn_runner_factory=lambda session, _profile_id: _ObservedRunner(
            session,
            started=started,
            first_started=first_started,
            release_first=release_first,
        ),
    )
    first_task = asyncio.create_task(
        orchestrator.run_turn(
            profile_id="default",
            session_id="s-queue",
            message="first",
            client_msg_id="msg-1",
            source="api",
        )
    )
    await asyncio.wait_for(first_started.wait(), timeout=1.0)
    second_task = asyncio.create_task(
        orchestrator.run_turn(
            profile_id="default",
            session_id="s-queue",
            message="second",
            client_msg_id="msg-2",
            source="api",
        )
    )
    await asyncio.sleep(0.1)
    assert started == ["first"]

    release_first.set()
    try:
        first, second = await asyncio.gather(first_task, second_task)
        assert first.envelope.message == "done:first"
        assert second.envelope.message == "done:second"
        assert started == ["first", "second"]
        async with session_scope(factory) as session:
            rows = (await session.execute(select(ChatSessionTurnQueueItem))).scalars().all()
            assert rows == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_orchestrator_turn_lease_keeps_external_turns_queued(
    tmp_path: Path,
) -> None:
    settings, engine, factory = await create_test_db(tmp_path, "session_orchestrator_lease.db")
    started: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="s-lease", profile_id="default")

    orchestrator = SessionOrchestrator(
        settings=settings,
        session_factory=factory,
        turn_runner_factory=lambda session, _profile_id: _ObservedRunner(
            session,
            started=started,
            first_started=first_started,
            release_first=release_first,
        ),
    )

    try:
        async with orchestrator.open_turn_lease(
            profile_id="default",
            session_id="s-lease",
            source="chat",
        ) as lease:
            first_task = asyncio.create_task(
                lease.run_turn(message="first")
            )
            await asyncio.wait_for(first_started.wait(), timeout=1.0)
            outside_task = asyncio.create_task(
                orchestrator.run_turn(
                    profile_id="default",
                    session_id="s-lease",
                    message="outside",
                    source="chat",
                )
            )
            await asyncio.sleep(0.1)
            assert started == ["first"]

            release_first.set()
            first = await asyncio.wait_for(first_task, timeout=1.0)
            assert first.envelope.message == "done:first"

            await asyncio.sleep(0.1)
            assert started == ["first"]

            second = await lease.run_turn(message="second")
            assert second.envelope.message == "done:second"
            assert started == ["first", "second"]
            assert outside_task.done() is False

        outside = await asyncio.wait_for(outside_task, timeout=1.0)
        assert outside.envelope.message == "done:outside"
        assert started == ["first", "second", "outside"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_orchestrator_turn_lease_serializes_internal_concurrent_calls(
    tmp_path: Path,
) -> None:
    settings, engine, factory = await create_test_db(
        tmp_path,
        "session_orchestrator_lease_internal.db",
    )
    started: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(
            session_id="s-lease-internal",
            profile_id="default",
        )

    orchestrator = SessionOrchestrator(
        settings=settings,
        session_factory=factory,
        turn_runner_factory=lambda session, _profile_id: _ObservedRunner(
            session,
            started=started,
            first_started=first_started,
            release_first=release_first,
        ),
    )

    try:
        async with orchestrator.open_turn_lease(
            profile_id="default",
            session_id="s-lease-internal",
            source="chat",
        ) as lease:
            first_task = asyncio.create_task(
                lease.run_turn(message="first")
            )
            await asyncio.wait_for(first_started.wait(), timeout=1.0)
            second_task = asyncio.create_task(
                lease.run_turn(message="second")
            )
            await asyncio.sleep(0.1)
            assert started == ["first"]

            release_first.set()
            first = await asyncio.wait_for(first_task, timeout=1.0)
            second = await asyncio.wait_for(second_task, timeout=1.0)
            assert first.envelope.message == "done:first"
            assert second.envelope.message == "done:second"
            assert started == ["first", "second"]
    finally:
        await engine.dispose()
