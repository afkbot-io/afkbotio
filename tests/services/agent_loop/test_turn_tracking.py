"""Tests for turn cancellation after session-level orchestration."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.session import session_scope
from afkbot.models.run import Run
from afkbot.models.runlog_event import RunlogEvent
from afkbot.repositories.run_repo import RunRepository
from afkbot.services.agent_loop.context_builder import ContextBuilder
from afkbot.services.agent_loop.loop import AgentLoop
from afkbot.services.session_orchestration import SessionOrchestrator
from afkbot.services.skills.skills import SkillLoader
from afkbot.services.tools.base import ToolCall
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import SlowTool, create_test_db


async def test_cancelling_orchestrated_turn_marks_run_cancelled(tmp_path: Path) -> None:
    """Cancelling the outer session turn task should cancel AgentLoop and finalize the run."""

    settings, engine, factory = await create_test_db(tmp_path, "orchestrated_outer_cancel.db")

    def _runner(session: AsyncSession, _profile_id: str) -> AgentLoop:
        return AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([SlowTool()]),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

    orchestrator = SessionOrchestrator(
        settings=settings,
        session_factory=factory,
        turn_runner_factory=_runner,
    )
    task = asyncio.create_task(
        orchestrator.run_turn(
            profile_id="default",
            session_id="s-orchestrated-cancel",
            message="hello",
            planned_tool_calls=[ToolCall(name="debug.slow", params={})],
        )
    )
    await asyncio.sleep(0.1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    async with session_scope(factory) as session:
        runs = (await session.execute(select(Run))).scalars().all()
        assert runs[0].status == "cancelled"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert events[-1].event_type == "turn.cancel"

    await engine.dispose()


async def test_cancel_request_marks_orchestrated_run_cancelled(tmp_path: Path) -> None:
    """Storage-backed cancellation should stop an orchestrated running turn."""

    settings, engine, factory = await create_test_db(tmp_path, "orchestrated_cross_cancel.db")

    def _runner(session: AsyncSession, _profile_id: str) -> AgentLoop:
        return AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([SlowTool()]),
            tool_timeout_default_sec=settings.tool_timeout_default_sec,
            tool_timeout_max_sec=settings.tool_timeout_max_sec,
        )

    orchestrator = SessionOrchestrator(
        settings=settings,
        session_factory=factory,
        turn_runner_factory=_runner,
    )
    task = asyncio.create_task(
        orchestrator.run_turn(
            profile_id="default",
            session_id="s-cross-cancel",
            message="hello",
            planned_tool_calls=[
                ToolCall(name="debug.slow", params={}),
                ToolCall(name="debug.slow", params={}),
            ],
        )
    )
    cancelled = False
    for _ in range(20):
        await asyncio.sleep(0.05)
        async with session_scope(factory) as cancel_session:
            cancelled = await RunRepository(cancel_session).request_cancel(
                profile_id="default",
                session_id="s-cross-cancel",
            )
        if cancelled:
            break
    assert cancelled is True

    with pytest.raises(asyncio.CancelledError):
        await task

    async with session_scope(factory) as session:
        runs = (await session.execute(select(Run))).scalars().all()
        assert runs[0].status == "cancelled"

        events = (
            (await session.execute(select(RunlogEvent).order_by(RunlogEvent.id.asc())))
            .scalars()
            .all()
        )
        assert events[-1].event_type == "turn.cancel"

    await engine.dispose()


async def test_cancelling_run_turn_task_marks_run_cancelled(tmp_path: Path) -> None:
    """Directly cancelling the AgentLoop task should still persist cancelled run state."""

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

        task.cancel()
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
