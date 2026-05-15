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
from afkbot.services.tools.base import ToolCall, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.registry import ToolRegistry
from tests.services.agent_loop._loop_harness import SlowTool, create_test_db


class _ObservedSlowTool(SlowTool):
    """Slow tool variant that marks the turn as safely inside tool execution."""

    def __init__(self, started: asyncio.Event) -> None:
        self._started = started

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        """Signal that the tool is running, then keep the base slow behavior."""

        self._started.set()
        return await super().execute(ctx, params)


async def _wait_for_run_started(
    factory,
    *,
    profile_id: str,
    session_id: str,
    timeout_sec: float = 2.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        async with session_scope(factory) as session:
            run = (
                await session.execute(
                    select(Run)
                    .where(
                        Run.profile_id == profile_id,
                        Run.session_id == session_id,
                    )
                    .order_by(Run.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if run is not None:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"Timed out waiting for run start: {profile_id}/{session_id}")


async def test_cancelling_orchestrated_turn_marks_run_cancelled(tmp_path: Path) -> None:
    """Cancelling the outer session turn task should cancel AgentLoop and finalize the run."""

    settings, engine, factory = await create_test_db(tmp_path, "orchestrated_outer_cancel.db")
    tool_started = asyncio.Event()

    def _runner(session: AsyncSession, _profile_id: str) -> AgentLoop:
        return AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([_ObservedSlowTool(tool_started)]),
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
    await _wait_for_run_started(
        factory,
        profile_id="default",
        session_id="s-orchestrated-cancel",
    )
    await asyncio.wait_for(tool_started.wait(), timeout=2.0)

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
    tool_started = asyncio.Event()

    def _runner(session: AsyncSession, _profile_id: str) -> AgentLoop:
        return AgentLoop(
            session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([_ObservedSlowTool(tool_started)]),
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
    await _wait_for_run_started(
        factory,
        profile_id="default",
        session_id="s-cross-cancel",
    )
    await asyncio.wait_for(tool_started.wait(), timeout=2.0)
    async with session_scope(factory) as cancel_session:
        cancelled = await RunRepository(cancel_session).request_cancel(
            profile_id="default",
            session_id="s-cross-cancel",
        )
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
    tool_started = asyncio.Event()

    async with session_scope(factory) as run_session:
        loop = AgentLoop(
            run_session,
            ContextBuilder(settings, SkillLoader(settings)),
            tool_registry=ToolRegistry([_ObservedSlowTool(tool_started)]),
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
        await asyncio.wait_for(tool_started.wait(), timeout=2.0)

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
