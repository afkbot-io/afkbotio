"""Lifecycle tests for subagent run/wait/result."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.repositories.subagent_task_repo import SubagentTaskRepository
from afkbot.models.chat_session import ChatSession
from afkbot.models.chat_turn import ChatTurn
from afkbot.models.subagent_task import SubagentTask
from afkbot.repositories.profile_policy_repo import ProfilePolicyRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.policy import PolicyViolationError
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.subagents.runner import SubagentExecutionError, SubagentExecutionResult, SubagentRunner
from afkbot.services.subagents.service import SubagentService
from afkbot.services.tools.base import ToolContext
from afkbot.settings import Settings


def _prepare_core_researcher(tmp_path: Path) -> None:
    path = tmp_path / "afkbot/subagents/researcher.md"
    path.parent.mkdir(parents=True)
    path.write_text("# researcher", encoding="utf-8")


class _PersistingRunner(SubagentRunner):
    """Test runner that simulates one persisted child-agent completion."""

    async def execute(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        task_id: str,
        profile_id: str,
        parent_session_id: str,
        subagent_name: str,
        subagent_markdown: str,
        prompt: str,
    ) -> SubagentExecutionResult:
        _ = parent_session_id, subagent_name, subagent_markdown
        child_session_id = f"subagent:{task_id}"
        async with session_scope(session_factory) as session:
            session.add(
                ChatSession(
                    id=child_session_id,
                    profile_id=profile_id,
                    title="Subagent Session",
                    status="active",
                )
            )
            await session.flush()
            session.add(
                ChatTurn(
                    session_id=child_session_id,
                    profile_id=profile_id,
                    user_message=prompt,
                    assistant_message="# researcher | hello",
                )
            )
            await session.flush()
        return SubagentExecutionResult(
            output="# researcher | hello",
            child_session_id=child_session_id,
            child_run_id=77,
        )


class _SleepingRunner(SubagentRunner):
    """Test runner that sleeps to exercise wait/timeout behavior."""

    def __init__(self, settings: Settings, *, seconds: float) -> None:
        super().__init__(settings)
        self._seconds = seconds

    async def execute(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        task_id: str,
        profile_id: str,
        parent_session_id: str,
        subagent_name: str,
        subagent_markdown: str,
        prompt: str,
    ) -> SubagentExecutionResult:
        _ = (
            session_factory,
            task_id,
            profile_id,
            parent_session_id,
            subagent_name,
            subagent_markdown,
            prompt,
        )
        await asyncio.sleep(self._seconds)
        return SubagentExecutionResult(
            output="done",
            child_session_id=f"subagent:{task_id}",
            child_run_id=99,
        )


async def _wait_terminal(
    service: SubagentService,
    *,
    task_id: str,
    profile_id: str,
    session_id: str,
    max_attempts: int = 30,
) -> str:
    """Poll wait endpoint until terminal status or attempts exhausted."""

    status = "running"
    for _ in range(max_attempts):
        response = await service.wait(
            task_id=task_id,
            timeout_sec=1,
            profile_id=profile_id,
            session_id=session_id,
        )
        status = response.status
        if response.done:
            return status
        await asyncio.sleep(0.1)
    return status


async def test_run_wait_result_completed(tmp_path: Path) -> None:
    """Subagent task should finish and return output via result endpoint."""

    _prepare_core_researcher(tmp_path)
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents1.db'}", root_dir=tmp_path)
    service = SubagentService(
        settings=settings,
        runner=_PersistingRunner(settings),
        launch_mode="inline",
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    accepted = await service.run(ctx=ctx, prompt="hello", subagent_name=None, timeout_sec=None)
    status = await _wait_terminal(
        service,
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )
    result = await service.result(
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )

    assert status == "completed"
    assert result.status == "completed"
    assert result.output is not None
    assert result.output == "# researcher | hello"
    assert result.child_session_id == f"subagent:{accepted.task_id}"
    assert result.child_run_id is not None

    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        child_session_id = f"subagent:{accepted.task_id}"
        task_row = await session.get(SubagentTask, accepted.task_id)
        child_session = await session.get(ChatSession, child_session_id)
        child_turns = list(
            (
                await session.execute(
                    select(ChatTurn).where(
                        ChatTurn.profile_id == ctx.profile_id,
                        ChatTurn.session_id == child_session_id,
                    )
                )
            ).scalars()
        )
    await engine.dispose()

    assert task_row is not None
    assert task_row.child_session_id == child_session_id
    assert task_row.child_run_id == result.child_run_id
    assert child_session is not None
    assert len(child_turns) == 1
    assert child_turns[0].user_message == "hello"
    assert child_turns[0].assistant_message == "# researcher | hello"
    await service.shutdown()


async def test_run_normalizes_profile_subagent_name_before_lookup(tmp_path: Path) -> None:
    """Runtime subagent execution should accept localized/profile labels and normalize them."""

    _prepare_core_researcher(tmp_path)
    profile_path = tmp_path / "profiles/default/subagents/analizator.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("# analizator", encoding="utf-8")
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents-normalized.db'}", root_dir=tmp_path)
    service = SubagentService(
        settings=settings,
        runner=_PersistingRunner(settings),
        launch_mode="inline",
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    accepted = await service.run(
        ctx=ctx,
        prompt="hello",
        subagent_name="Анализатор",
        timeout_sec=None,
    )

    assert accepted.subagent_name == "analizator"
    await _wait_terminal(
        service,
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )
    await service.shutdown()


async def test_run_missing_subagent_lists_available_runtime_names(tmp_path: Path) -> None:
    """Lookup failures should point the caller at the visible runtime subagent surface."""

    _prepare_core_researcher(tmp_path)
    profile_path = tmp_path / "profiles/default/subagents/poet-10-lines.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("# poet-10-lines", encoding="utf-8")
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents-missing.db'}", root_dir=tmp_path)
    service = SubagentService(
        settings=settings,
        runner=_PersistingRunner(settings),
        launch_mode="inline",
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    with pytest.raises(FileNotFoundError) as exc_info:
        await service.run(
            ctx=ctx,
            prompt="hello",
            subagent_name="Hubble",
            timeout_sec=None,
        )

    reason = str(exc_info.value)
    assert "Subagent not found: Hubble (normalized: hubble)" in reason
    assert "poet-10-lines" in reason
    assert "researcher" in reason
    await service.shutdown()


async def test_result_before_completion_returns_running(tmp_path: Path) -> None:
    """Result before completion should return running state and not-finished error code."""

    _prepare_core_researcher(tmp_path)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents2.db'}",
        root_dir=tmp_path,
        subagent_timeout_default_sec=2,
        subagent_timeout_max_sec=2,
    )
    service = SubagentService(
        settings=settings,
        runner=_SleepingRunner(settings, seconds=0.5),
        launch_mode="inline",
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    accepted = await service.run(ctx=ctx, prompt="hello", subagent_name=None, timeout_sec=None)
    current = await service.result(
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )
    assert current.status == "running"
    assert current.error_code == "subagent_not_finished"
    await _wait_terminal(
        service,
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )
    await service.shutdown()


async def test_subagent_timeout(tmp_path: Path) -> None:
    """Task should move to timeout state when runner exceeds timeout."""

    _prepare_core_researcher(tmp_path)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents3.db'}",
        root_dir=tmp_path,
        subagent_timeout_default_sec=1,
        subagent_timeout_max_sec=1,
    )
    service = SubagentService(
        settings=settings,
        runner=_SleepingRunner(settings, seconds=2.0),
        launch_mode="inline",
    )
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    accepted = await service.run(ctx=ctx, prompt="hello", subagent_name=None, timeout_sec=None)
    status = await _wait_terminal(
        service,
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )
    result = await service.result(
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )

    assert status == "timeout"
    assert result.status == "timeout"
    assert result.error_code == "subagent_timeout"
    await service.shutdown()


async def test_subagent_run_respects_profile_policy(tmp_path: Path) -> None:
    """Subagent runtime should be blocked when policy disables subagent iterations."""

    _prepare_core_researcher(tmp_path)
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents4.db'}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        policy = await ProfilePolicyRepository(session).get_or_create_default("default")
        policy.max_iterations_subagent = 0
        await session.flush()
    await engine.dispose()

    service = SubagentService(settings=settings)
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    with pytest.raises(PolicyViolationError, match="max_iterations_subagent <= 0"):
        await service.run(ctx=ctx, prompt="hello", subagent_name=None, timeout_sec=None)


async def test_subagent_run_fails_without_configured_llm(tmp_path: Path) -> None:
    """Default runner should fail deterministically when child profile has no provider key."""

    _prepare_core_researcher(tmp_path)
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents5.db'}",
        root_dir=tmp_path,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        openai_api_key=None,
        llm_api_key=None,
    )
    service = SubagentService(settings=settings, launch_mode="inline")
    ctx = ToolContext(profile_id="default", session_id="s-1", run_id=1)

    accepted = await service.run(ctx=ctx, prompt="hello", subagent_name=None, timeout_sec=None)
    status = await _wait_terminal(
        service,
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )
    result = await service.result(
        task_id=accepted.task_id,
        profile_id=ctx.profile_id,
        session_id=ctx.session_id,
    )

    assert status == "failed"
    assert result.status == "failed"
    assert result.error_code == "subagent_llm_not_configured"
    assert result.output is None
    await service.shutdown()


def test_custom_subagent_runner_requires_inline_launch_mode(tmp_path: Path) -> None:
    """Process launch mode should reject custom runner injection up front."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents6.db'}", root_dir=tmp_path)

    with pytest.raises(ValueError, match="launch_mode='inline'"):
        SubagentService(settings=settings, runner=_PersistingRunner(settings))


async def test_subagent_runner_raises_when_child_runlog_contains_llm_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default child runner should fail fast when child run finalized through LLM error path."""

    # Arrange
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents7.db'}",
        root_dir=tmp_path,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        openai_api_key="test-key",
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    class _FailingChildLoop:
        def __init__(self, session: AsyncSession) -> None:
            self._session = session

        async def run_turn(  # type: ignore[no-untyped-def]
            self,
            *,
            profile_id: str,
            session_id: str,
            message: str,
            context_overrides=None,
            **_unused: object,
        ) -> TurnResult:
            _ = message, context_overrides
            sessions = ChatSessionRepository(self._session)
            if await sessions.get(session_id) is None:
                await sessions.create(session_id=session_id, profile_id=profile_id)
            run = await RunRepository(self._session).create_run(
                session_id=session_id,
                profile_id=profile_id,
                status="running",
            )
            await RunlogRepository(self._session).create_event(
                run_id=run.id,
                session_id=session_id,
                event_type="llm.call.error",
                payload={
                    "error_code": "llm_provider_error",
                    "reason": "ConnectionError: upstream reset",
                },
            )
            return TurnResult(
                run_id=run.id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(
                    action="finalize",
                    message="LLM provider failed before planning could complete.",
                ),
            )

    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_factory.resolve_profile_settings",
        lambda settings, profile_id, ensure_layout=False: settings,
    )
    captured_profile_ids: list[str | None] = []

    def _build_failing_child_loop(session, settings, actor, profile_id=None):  # type: ignore[no-untyped-def]
        _ = settings, actor
        captured_profile_ids.append(profile_id)
        return _FailingChildLoop(session)

    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_factory.build_agent_loop_from_settings",
        _build_failing_child_loop,
    )

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        session.add(
            ChatSession(
                id="subagent:task-1",
                profile_id="default",
                title="Subagent Session",
                status="active",
            )
        )
        await session.flush()

    runner = SubagentRunner(settings)

    # Act
    with pytest.raises(SubagentExecutionError, match="ConnectionError: upstream reset") as exc_info:
        await runner.execute(
            session_factory=factory,
            task_id="task-1",
            profile_id="default",
            parent_session_id="main-session",
            subagent_name="researcher",
            subagent_markdown="# researcher",
            prompt="hello",
        )

    # Assert
    await engine.dispose()
    assert captured_profile_ids == ["default"]
    assert exc_info.value.error_code == "llm_provider_error"


async def test_subagent_runner_stops_child_turn_after_cross_instance_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisted cancel from another service instance should stop the active child turn."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents-cancel.db'}",
        root_dir=tmp_path,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        openai_api_key="test-key",
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    class _CancellableChildLoop:
        def __init__(self, session: AsyncSession) -> None:
            self._session = session

        async def run_turn(  # type: ignore[no-untyped-def]
            self,
            *,
            profile_id: str,
            session_id: str,
            message: str,
            context_overrides=None,
            **_unused: object,
        ) -> TurnResult:
            _ = message, context_overrides
            sessions = ChatSessionRepository(self._session)
            if await sessions.get(session_id) is None:
                await sessions.create(session_id=session_id, profile_id=profile_id)
            run = await RunRepository(self._session).create_run(
                session_id=session_id,
                profile_id=profile_id,
                status="running",
            )
            await self._session.commit()
            while True:
                await self._session.commit()
                if await RunRepository(self._session).is_cancel_requested(run.id):
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.05)

    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_factory.resolve_profile_settings",
        lambda settings, profile_id, ensure_layout=False: settings,
    )
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_factory.build_agent_loop_from_settings",
        lambda session, settings, actor, profile_id=None: _CancellableChildLoop(session),
    )

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        session.add(
            SubagentTask(
                task_id="task-cancel",
                profile_id="default",
                session_id="parent-session",
                run_id=1,
                subagent_name="researcher",
                prompt="hello",
                timeout_sec=30,
                status="running",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()

    runner = SubagentRunner(settings)
    worker_task = asyncio.create_task(
        runner.execute(
            session_factory=factory,
            task_id="task-cancel",
            profile_id="default",
            parent_session_id="parent-session",
            subagent_name="researcher",
            subagent_markdown="# researcher",
            prompt="hello",
        )
    )
    canceller_service = SubagentService(settings=settings)
    try:
        await asyncio.sleep(0.2)
        cancelled = await canceller_service.cancel(
            task_id="task-cancel",
            profile_id="default",
            session_id="parent-session",
        )
        assert cancelled.status == "cancelled"
        with pytest.raises(SubagentExecutionError, match="Subagent task was cancelled") as exc_info:
            await asyncio.wait_for(worker_task, timeout=2.0)
        assert exc_info.value.error_code == "subagent_cancelled"
    finally:
        await canceller_service.shutdown()
        await engine.dispose()


async def test_subagent_runner_preserves_external_timeout_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cross-instance timeout should stop the child turn without downgrading the terminal reason."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'subagents-timeout-forward.db'}",
        root_dir=tmp_path,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        openai_api_key="test-key",
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    class _CancellableChildLoop:
        def __init__(self, session: AsyncSession) -> None:
            self._session = session

        async def run_turn(  # type: ignore[no-untyped-def]
            self,
            *,
            profile_id: str,
            session_id: str,
            message: str,
            context_overrides=None,
            **_unused: object,
        ) -> TurnResult:
            _ = message, context_overrides
            sessions = ChatSessionRepository(self._session)
            if await sessions.get(session_id) is None:
                await sessions.create(session_id=session_id, profile_id=profile_id)
            run = await RunRepository(self._session).create_run(
                session_id=session_id,
                profile_id=profile_id,
                status="running",
            )
            await self._session.commit()
            while True:
                await self._session.commit()
                if await RunRepository(self._session).is_cancel_requested(run.id):
                    raise asyncio.CancelledError()
                await asyncio.sleep(0.05)

    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_factory.resolve_profile_settings",
        lambda settings, profile_id, ensure_layout=False: settings,
    )
    monkeypatch.setattr(
        "afkbot.services.agent_loop.runtime_factory.build_agent_loop_from_settings",
        lambda session, settings, actor, profile_id=None: _CancellableChildLoop(session),
    )

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        session.add(
            SubagentTask(
                task_id="task-timeout",
                profile_id="default",
                session_id="parent-session",
                run_id=1,
                subagent_name="researcher",
                prompt="hello",
                timeout_sec=30,
                status="running",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()

    runner = SubagentRunner(settings)
    worker_task = asyncio.create_task(
        runner.execute(
            session_factory=factory,
            task_id="task-timeout",
            profile_id="default",
            parent_session_id="parent-session",
            subagent_name="researcher",
            subagent_markdown="# researcher",
            prompt="hello",
        )
    )
    try:
        await asyncio.sleep(0.2)
        async with session_scope(factory) as session:
            await SubagentTaskRepository(session).finish_task(
                task_id="task-timeout",
                status="timeout",
                finished_at=datetime.now(timezone.utc),
                child_session_id=None,
                child_run_id=None,
                output=None,
                error_code="subagent_timeout",
                reason="Subagent timed out after 30 seconds",
            )
        with pytest.raises(SubagentExecutionError, match="Subagent timed out after 30 seconds") as exc_info:
            await asyncio.wait_for(worker_task, timeout=2.0)
        assert exc_info.value.error_code == "subagent_timeout"
    finally:
        await engine.dispose()
