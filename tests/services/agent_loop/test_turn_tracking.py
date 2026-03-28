"""Tests for tracked turn lifecycle and cancellation behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from afkbot.db.session import session_scope
from afkbot.models.run import Run
from afkbot.models.runlog_event import RunlogEvent
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolCall
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import SlowTool, create_test_db


async def test_cancel_active_tracked_turn(tmp_path: Path) -> None:
    """Cancelling tracked turn should stop task and mark run cancelled."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_cancel.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([SlowTool()]),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        task_id, task = await loop.start_tracked_turn(
            profile_id="default",
            session_id="s-cancel",
            message="hello",
            planned_tool_calls=[ToolCall(name="debug.slow", params={})],
        )
        assert isinstance(task_id, str)

        await asyncio.sleep(0.1)
        cancelled = await loop.cancel_turn(profile_id="default", session_id="s-cancel")
        assert cancelled is True

        with pytest.raises(asyncio.CancelledError):
            await task

        runs = (await session.execute(select(Run))).scalars().all()
        assert runs[0].status == "cancelled"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert events[-1].event_type == "turn.cancel"

    await engine.dispose()


async def test_start_tracked_turn_rejects_parallel_same_session(tmp_path: Path) -> None:
    """Second tracked turn for same session should fail while first is active."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_parallel_tracked.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([SlowTool()]),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        _, first_task = await loop.start_tracked_turn(
            profile_id="default",
            session_id="s-parallel",
            message="hello",
            planned_tool_calls=[ToolCall(name="debug.slow", params={})],
        )

        with pytest.raises(RuntimeError, match="turn_already_running"):
            await loop.start_tracked_turn(
                profile_id="default",
                session_id="s-parallel",
                message="hello 2",
                planned_tool_calls=[ToolCall(name="debug.slow", params={})],
            )

        await loop.cancel_turn(profile_id="default", session_id="s-parallel")
        with pytest.raises(asyncio.CancelledError):
            await first_task

    await engine.dispose()


async def test_start_tracked_turn_rejects_parallel_when_started_concurrently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent start requests for same session should admit only one active turn."""

    settings, engine, factory = await create_test_db(
        tmp_path,
        "loop_parallel_tracked_concurrent.db",
    )

    async with session_scope(factory) as session:
        loop = AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([SlowTool()]),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

        async def _run_turn_stub(**kwargs: object) -> object:
            _ = kwargs
            await asyncio.sleep(1.0)
            return object()

        monkeypatch.setattr(loop, "run_turn", _run_turn_stub)

        async def _start() -> tuple[str, asyncio.Task[object]] | Exception:
            try:
                return await loop.start_tracked_turn(
                    profile_id="default",
                    session_id="s-parallel-concurrent",
                    message="hello",
                    planned_tool_calls=[ToolCall(name="debug.slow", params={})],
                )
            except Exception as exc:  # pragma: no cover - returned for assertion
                return exc

        first, second = await asyncio.gather(_start(), _start())
        outcomes = [first, second]
        successes = [item for item in outcomes if not isinstance(item, Exception)]
        failures = [item for item in outcomes if isinstance(item, Exception)]

        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], RuntimeError)
        assert str(failures[0]) == "turn_already_running"

        task = successes[0][1]
        await loop.cancel_turn(profile_id="default", session_id="s-parallel-concurrent")
        with pytest.raises(asyncio.CancelledError):
            await task

    await engine.dispose()


async def test_cancel_turn_returns_false_for_stale_completed_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelling stale completed tracked task should return False."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_cancel_stale_done.db")

    async with session_scope(factory) as session:
        loop = AgentLoop(session, ContextBuilder(settings, SkillLoader(settings)))

        async def _skip_cleanup(*, profile_id: str, session_id: str, task_id: str) -> None:
            _ = (profile_id, session_id, task_id)

        monkeypatch.setattr(loop._turn_tracker, "cleanup_by_task_id", _skip_cleanup)

        _, task = await loop.start_tracked_turn(
            profile_id="default",
            session_id="s-stale",
            message="hello",
        )
        await task

        cancelled = await loop.cancel_turn(profile_id="default", session_id="s-stale")
        assert cancelled is False

    await engine.dispose()


async def test_cancel_request_marks_run_cancelled(tmp_path: Path) -> None:
    """Storage-backed cancellation request should stop running turn and mark cancelled status."""

    settings, engine, factory = await create_test_db(tmp_path, "loop_cross_cancel.db")

    async with session_scope(factory) as run_session:
        loop = AgentLoop(
            run_session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([SlowTool()]),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        _, task = await loop.start_tracked_turn(
            profile_id="default",
            session_id="s-cross-cancel",
            message="hello",
            planned_tool_calls=[
                ToolCall(name="debug.slow", params={}),
                ToolCall(name="debug.slow", params={}),
            ],
        )

        await asyncio.sleep(0.1)
        async with session_scope(factory) as cancel_session:
            cancelled = await RunRepository(cancel_session).request_cancel(
                profile_id="default",
                session_id="s-cross-cancel",
            )
        assert cancelled is True

        with pytest.raises(asyncio.CancelledError):
            await task

        runs = (await run_session.execute(select(Run))).scalars().all()
        assert runs[0].status == "cancelled"

        events = (
            (await run_session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert events[-1].event_type == "turn.cancel"

    await engine.dispose()


async def test_cancelling_run_turn_task_marks_run_cancelled(tmp_path: Path) -> None:
    """Directly cancelling the active run_turn task should still persist cancelled run state."""

    # Arrange
    settings, engine, factory = await create_test_db(tmp_path, "loop_outer_cancel.db")

    async with session_scope(factory) as run_session:
        loop = AgentLoop(
            run_session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([SlowTool()]),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )
        task = asyncio.create_task(
            loop.run_turn(
                profile_id="default",
                session_id="s-outer-cancel",
                message="hello",
                planned_tool_calls=[ToolCall(name="debug.slow", params={})],
            )
        )
        await asyncio.sleep(0.1)

        # Act
        task.cancel()

        # Assert
        with pytest.raises(asyncio.CancelledError):
            await task

        runs = (await run_session.execute(select(Run))).scalars().all()
        assert runs[0].status == "cancelled"

        events = (
            (await run_session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert events[-1].event_type == "turn.cancel"

    await engine.dispose()
