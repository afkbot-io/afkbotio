"""Tests for canonical session turn orchestration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
import sqlite3

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.session import session_scope
from afkbot.models.chat_session_turn_queue import ChatSessionTurnQueueItem
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.chat_session_turn_queue_repo import ChatSessionTurnQueueRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.session_orchestration import SessionOrchestrator
from afkbot.services.session_orchestration.service import _session_queue_poll_delay
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


def test_session_queue_poll_delay_uses_capped_backoff() -> None:
    """Session queue wait loop should reduce DB churn while preserving short first retry."""

    assert _session_queue_poll_delay(0) == pytest.approx(0.05)
    assert _session_queue_poll_delay(1) == pytest.approx(0.1)
    assert _session_queue_poll_delay(2) == pytest.approx(0.2)
    assert _session_queue_poll_delay(3) == pytest.approx(0.4)
    assert _session_queue_poll_delay(4) == pytest.approx(0.5)
    assert _session_queue_poll_delay(8) == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_session_orchestrator_retries_transient_sqlite_lock_during_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue release should retry transient SQLite lock errors instead of crashing chat."""

    settings, engine, factory = await create_test_db(
        tmp_path,
        "session_orchestrator_release_retry.db",
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    release_calls = {"count": 0}

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="s-release", profile_id="default")

    original_release = ChatSessionTurnQueueRepository.release

    async def _flaky_release(
        self: ChatSessionTurnQueueRepository,
        *,
        queue_item_id: int,
        owner_token: str,
    ) -> None:
        release_calls["count"] += 1
        if release_calls["count"] == 1:
            raise OperationalError(
                "DELETE FROM chat_session_turn_queue WHERE id = ? AND owner_token = ?",
                (queue_item_id, owner_token),
                sqlite3.OperationalError("database is locked"),
            )
        await original_release(
            self,
            queue_item_id=queue_item_id,
            owner_token=owner_token,
        )

    monkeypatch.setattr(ChatSessionTurnQueueRepository, "release", _flaky_release)

    orchestrator = SessionOrchestrator(
        settings=settings,
        session_factory=factory,
        turn_runner_factory=lambda session, _profile_id: _ObservedRunner(
            session,
            started=[],
            first_started=first_started,
            release_first=release_first,
        ),
    )

    try:
        result = await orchestrator.run_turn(
            profile_id="default",
            session_id="s-release",
            message="single",
            source="chat",
        )
        assert result.envelope.message == "done:single"
        assert release_calls["count"] >= 2
        async with session_scope(factory) as session:
            rows = (await session.execute(select(ChatSessionTurnQueueItem))).scalars().all()
            assert rows == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_orchestrator_wait_loop_throttles_stale_queue_sweeps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Waiting for queue admission should not execute stale cleanup on every poll tick."""

    settings, engine, factory = await create_test_db(
        tmp_path,
        "session_orchestrator_stale_sweep.db",
    )
    release_stale_calls = {"count": 0}

    async def _count_release_stale(
        self: ChatSessionTurnQueueRepository,
        *,
        older_than,
        profile_id: str,
        session_id: str,
    ) -> int:
        _ = self, older_than, profile_id, session_id
        release_stale_calls["count"] += 1
        return 0

    async def _never_mark_running(
        self: ChatSessionTurnQueueRepository,
        *,
        queue_item_id: int,
        profile_id: str,
        session_id: str,
        owner_token: str,
        touched_at,
    ) -> bool:
        _ = self, queue_item_id, profile_id, session_id, owner_token, touched_at
        return False

    monkeypatch.setattr(ChatSessionTurnQueueRepository, "release_stale", _count_release_stale)
    monkeypatch.setattr(ChatSessionTurnQueueRepository, "try_mark_running", _never_mark_running)

    orchestrator = SessionOrchestrator(settings=settings, session_factory=factory)
    waiter = asyncio.create_task(
        orchestrator._wait_until_marker_is_running(
            session_factory=factory,
            queue_item_id=1,
            profile_id="default",
            session_id="s-stale",
            owner_token="owner",
        )
    )

    try:
        await asyncio.sleep(0.26)
    finally:
        waiter.cancel()
        with suppress(asyncio.CancelledError):
            await waiter
        await engine.dispose()

    assert release_stale_calls["count"] == 1
