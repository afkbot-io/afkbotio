"""Lifecycle tests for the Task Flow runtime daemon."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.services.task_flow.runtime_daemon import TaskFlowRuntimeDaemon
from afkbot.services.task_flow.runtime_service import TaskFlowRuntimeService
from afkbot.services.task_flow.service import TaskFlowService
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.settings import Settings
from tests.repositories._harness import build_repository_factory


class _FakeTaskFlowRuntimeService:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0
        self.worker_ids: list[str] = []

    async def start(self) -> None:
        self.started += 1

    async def shutdown(self) -> None:
        self.stopped += 1

    async def execute_next_claimable_task(self, *, worker_id: str) -> bool:
        self.worker_ids.append(worker_id)
        await asyncio.sleep(0)
        return False


class _FlakyTaskFlowRuntimeService(_FakeTaskFlowRuntimeService):
    def __init__(self) -> None:
        super().__init__()
        self.execute_calls = 0

    async def execute_next_claimable_task(self, *, worker_id: str) -> bool:
        self.execute_calls += 1
        self.worker_ids.append(worker_id)
        await asyncio.sleep(0)
        if self.execute_calls == 1:
            raise RuntimeError("simulated worker failure")
        return False


async def _slow_cancel_worker_loop(*, worker_index: int) -> None:
    del worker_index
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        await asyncio.sleep(0.02)
        raise


class _CompletingLoop:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: object | None = None,
    ) -> TurnResult:
        del message, context_overrides
        sessions = ChatSessionRepository(self._session)
        if await sessions.get(session_id) is None:
            await sessions.create(session_id=session_id, profile_id=profile_id)
        run = await RunRepository(self._session).create_run(
            session_id=session_id,
            profile_id=profile_id,
            status="completed",
        )
        await RunlogRepository(self._session).create_event(
            run_id=run.id,
            session_id=session_id,
            event_type="turn.finalize",
            payload={
                "assistant_message": "task complete",
                "blocked_reason": None,
                "state": "finalized",
            },
        )
        return TurnResult(
            run_id=run.id,
            session_id=session_id,
            profile_id=profile_id,
            envelope=ActionEnvelope(action="finalize", message="task complete"),
        )


class _CompletingSessionRunner:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: object | None = None,
        **_unused: object,
    ) -> TurnResult:
        async with session_scope(self._session_factory) as session:
            return await _CompletingLoop(session).run_turn(
                profile_id=profile_id,
                session_id=session_id,
                message=message,
                context_overrides=context_overrides,
            )


async def test_taskflow_runtime_daemon_polls_workers_and_stops_cleanly(tmp_path: Path) -> None:
    """Daemon should start service once, poll workers, and stop cleanly."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_daemon.db'}",
        runtime_worker_count=2,
        taskflow_runtime_poll_interval_sec=0.01,
        runtime_shutdown_timeout_sec=1.0,
    )
    service = _FakeTaskFlowRuntimeService()
    daemon = TaskFlowRuntimeDaemon(settings=settings, service=service)

    await daemon.start()
    try:
        await asyncio.sleep(0.05)
    finally:
        await daemon.stop()

    assert service.started == 1
    assert service.stopped == 1
    assert service.worker_ids
    assert set(service.worker_ids) <= {"taskflow-runtime:0", "taskflow-runtime:1"}


async def test_taskflow_runtime_daemon_survives_worker_errors(tmp_path: Path) -> None:
    """Worker loop should keep polling after one unexpected runtime exception."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_daemon_errors.db'}",
        runtime_worker_count=1,
        taskflow_runtime_poll_interval_sec=0.01,
        runtime_shutdown_timeout_sec=1.0,
    )
    service = _FlakyTaskFlowRuntimeService()
    daemon = TaskFlowRuntimeDaemon(settings=settings, service=service)

    await daemon.start()
    try:
        await asyncio.sleep(0.05)
    finally:
        await daemon.stop()

    assert service.started == 1
    assert service.stopped == 1
    assert service.execute_calls >= 2
    assert service.worker_ids
    assert set(service.worker_ids) == {"taskflow-runtime:0"}


async def test_taskflow_runtime_daemon_waits_for_cancelled_workers_before_shutdown(
    tmp_path: Path,
) -> None:
    """Shutdown should keep draining cancelled workers before clearing runtime state."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_daemon_slow_stop.db'}",
        runtime_worker_count=1,
        taskflow_runtime_poll_interval_sec=0.01,
        runtime_shutdown_timeout_sec=0.01,
    )
    service = _FakeTaskFlowRuntimeService()
    daemon = TaskFlowRuntimeDaemon(settings=settings, service=service)
    daemon._worker_loop = _slow_cancel_worker_loop  # type: ignore[method-assign]

    await daemon.start()
    await daemon.stop()

    assert service.started == 1
    assert service.stopped == 1
    assert daemon._worker_tasks == []


async def test_taskflow_runtime_daemon_executes_claimable_tasks_end_to_end(
    tmp_path: Path,
) -> None:
    """Daemon should poll and complete AI-owned tasks without an interactive terminal."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_daemon_execute.db",
        profile_ids=("default", "analyst"),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_daemon_execute.db'}",
        runtime_worker_count=1,
        taskflow_runtime_poll_interval_sec=0.01,
        runtime_shutdown_timeout_sec=1.0,
    )
    runtime_service = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _CompletingSessionRunner(session),
    )
    daemon = TaskFlowRuntimeDaemon(settings=settings, service=runtime_service)
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Background analyze inbox",
            prompt="Analyze the backlog in the background and persist the result.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )

        await daemon.start()
        try:
            for _ in range(50):
                updated = await service.get_task(profile_id="default", task_id=task.id)
                if updated.status == "completed":
                    break
                await asyncio.sleep(0.02)
            else:
                raise AssertionError("Task Flow daemon did not complete claimed work in time")
        finally:
            await daemon.stop()

        completed = await service.get_task(profile_id="default", task_id=task.id)
        assert completed.status == "completed"
        assert completed.last_session_id == f"taskflow:{task.id}"
        assert completed.last_run_id is not None
    finally:
        await engine.dispose()
