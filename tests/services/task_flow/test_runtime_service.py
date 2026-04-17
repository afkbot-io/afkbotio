"""Tests for detached Task Flow runtime execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.models.task_run import TaskRun
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.task_flow.context_overrides import build_task_flow_context_overrides
from afkbot.services.task_flow.runtime_service import TaskFlowRuntimeService
from afkbot.services.task_flow.service import TaskFlowService
from afkbot.settings import Settings
from tests.repositories._harness import build_repository_factory


@dataclass
class _ObservedCall:
    profile_id: str
    session_id: str
    message: str
    transport: str | None
    account_id: str | None
    task_id: str | None
    task_profile_id: str | None
    prompt_overlay: str | None


class _FakeLoop:
    def __init__(
        self,
        session: AsyncSession,
        *,
        behavior: str,
        observed_calls: list[_ObservedCall],
    ) -> None:
        self._session = session
        self._behavior = behavior
        self._observed_calls = observed_calls

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: object | None = None,
    ) -> TurnResult:
        metadata = (
            context_overrides.runtime_metadata
            if isinstance(context_overrides, TurnContextOverrides)
            else None
        )
        taskflow_payload = metadata.get("taskflow") if isinstance(metadata, dict) else None
        self._observed_calls.append(
            _ObservedCall(
                profile_id=profile_id,
                session_id=session_id,
                message=message,
                transport=(
                    str(metadata.get("transport") or "").strip() if isinstance(metadata, dict) else None
                ),
                account_id=(
                    str(metadata.get("account_id") or "").strip()
                    if isinstance(metadata, dict)
                    else None
                ),
                task_id=(
                    str(taskflow_payload.get("task_id") or "").strip()
                    if isinstance(taskflow_payload, dict)
                    else None
                ),
                task_profile_id=(
                    str(taskflow_payload.get("task_profile_id") or "").strip()
                    if isinstance(taskflow_payload, dict)
                    else None
                ),
                prompt_overlay=(
                    context_overrides.prompt_overlay
                    if isinstance(context_overrides, TurnContextOverrides)
                    else None
                ),
            )
        )
        sessions = ChatSessionRepository(self._session)
        if await sessions.get(session_id) is None:
            await sessions.create(session_id=session_id, profile_id=profile_id)
        run = await RunRepository(self._session).create_run(
            session_id=session_id,
            profile_id=profile_id,
            status="completed",
        )
        runlog = RunlogRepository(self._session)
        if self._behavior == "complete":
            await runlog.create_event(
                run_id=run.id,
                session_id=session_id,
                event_type="turn.finalize",
                payload={
                    "assistant_message": "analysis complete",
                    "blocked_reason": None,
                    "state": "finalized",
                },
            )
            return TurnResult(
                run_id=run.id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(action="finalize", message="analysis complete"),
            )
        if self._behavior == "ask_question":
            await runlog.create_event(
                run_id=run.id,
                session_id=session_id,
                event_type="turn.ask_question",
                payload={"assistant_message": "Need human approval"},
            )
            return TurnResult(
                run_id=run.id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(
                    action="ask_question",
                    message="Need human approval",
                    question_id="q-review",
                ),
            )
        if self._behavior == "handoff_human":
            assert isinstance(taskflow_payload, dict)
            updated = await TaskFlowRepository(self._session).update_task(
                profile_id=str(taskflow_payload.get("task_profile_id") or ""),
                task_id=str(taskflow_payload.get("task_id") or ""),
                owner_type="human",
                owner_ref="cli_user:alice",
                status="review",
                blocked_reason_code="awaiting_human_review",
                blocked_reason_text="Ready for human review.",
            )
            assert updated is not None
            await runlog.create_event(
                run_id=run.id,
                session_id=session_id,
                event_type="turn.finalize",
                payload={
                    "assistant_message": "Handoff completed",
                    "blocked_reason": None,
                    "state": "finalized",
                },
            )
            return TurnResult(
                run_id=run.id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(action="finalize", message="Handoff completed"),
            )
        if self._behavior == "dependency_wait":
            assert isinstance(taskflow_payload, dict)
            updated = await TaskFlowRepository(self._session).update_task(
                profile_id=str(taskflow_payload.get("task_profile_id") or ""),
                task_id=str(taskflow_payload.get("task_id") or ""),
                status="blocked",
                blocked_reason_code="dependency_wait",
                blocked_reason_text="Waiting for delegated task completion.",
            )
            assert updated is not None
            await runlog.create_event(
                run_id=run.id,
                session_id=session_id,
                event_type="turn.finalize",
                payload={
                    "assistant_message": "Delegated follow-up created",
                    "blocked_reason": None,
                    "state": "finalized",
                },
            )
            return TurnResult(
                run_id=run.id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(action="finalize", message="Delegated follow-up created"),
            )
        if self._behavior == "llm_timeout":
            await runlog.create_event(
                run_id=run.id,
                session_id=session_id,
                event_type="llm.call.timeout",
                payload={"error_code": "llm_timeout"},
            )
            await runlog.create_event(
                run_id=run.id,
                session_id=session_id,
                event_type="turn.finalize",
                payload={
                    "assistant_message": "finalized: timeout",
                    "blocked_reason": None,
                    "state": "finalized",
                },
            )
            return TurnResult(
                run_id=run.id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(action="finalize", message="finalized: timeout"),
            )
        raise AssertionError(f"Unsupported fake loop behavior: {self._behavior}")


class _FakeSessionRunner:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        behavior: str,
        observed_calls: list[_ObservedCall],
    ) -> None:
        self._session_factory = session_factory
        self._behavior = behavior
        self._observed_calls = observed_calls

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
            return await _FakeLoop(
                session,
                behavior=self._behavior,
                observed_calls=self._observed_calls,
            ).run_turn(
                profile_id=profile_id,
                session_id=session_id,
                message=message,
                context_overrides=context_overrides,
            )


async def test_taskflow_runtime_executes_ai_owned_task_and_unblocks_dependents(
    tmp_path: Path,
) -> None:
    """Detached runtime should complete AI-owned work and unblock dependent tasks."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_complete.db",
        profile_ids=("default", "analyst"),
    )
    observed_calls: list[_ObservedCall] = []
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_complete.db'}",
    )
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
    )
    service = TaskFlowService(factory)
    try:
        first = await service.create_task(
            profile_id="default",
            title="Analyze support backlog",
            description="Summarize the last ten support tickets.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )
        dependent = await service.create_task(
            profile_id="default",
            title="Send triage summary",
            description="Send the triage summary after analysis is ready.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            depends_on_task_ids=(first.id,),
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-a")

        assert processed is True
        updated = await service.get_task(profile_id="default", task_id=first.id)
        assert updated.status == "completed"
        assert updated.last_session_id == f"taskflow:{first.id}"
        assert updated.last_run_id is not None
        listed_runs = await service.list_task_runs(profile_id="default", task_id=first.id)
        assert len(listed_runs) == 1
        assert listed_runs[0].id == updated.last_run_id
        assert listed_runs[0].status == "completed"
        listed_events = await service.list_task_events(profile_id="default", task_id=first.id)
        execution_event = next(item for item in listed_events if item.event_type == "execution_completed")
        assert execution_event.actor_type == "runtime"
        assert execution_event.actor_ref == "worker-a"
        assert execution_event.to_status == "completed"
        assert execution_event.details["run_id"] == listed_runs[0].run_id
        assert any(item.event_type == "comment_added" for item in listed_events)
        fallback_comment = next(item for item in listed_events if item.event_type == "comment_added")
        assert fallback_comment.message == "Completed: analysis complete"
        fetched_run = await service.get_task_run(
            profile_id="default",
            task_run_id=updated.last_run_id,
        )
        assert fetched_run.id == updated.last_run_id
        assert fetched_run.session_id == f"taskflow:{first.id}"
        assert fetched_run.run_id is not None
        unblocked = await service.get_task(profile_id="default", task_id=dependent.id)
        assert unblocked.status == "todo"
        assert len(observed_calls) == 1
        observed = observed_calls[0]
        assert observed.profile_id == "analyst"
        assert observed.session_id == f"taskflow:{first.id}"
        assert observed.transport == "taskflow"
        assert observed.account_id == first.id
        assert observed.task_id == first.id
        assert observed.task_profile_id == "default"
        assert observed.prompt_overlay is not None
        assert "Task Flow execution context." in observed.prompt_overlay
        assert "This runtime is non-interactive." in observed.prompt_overlay
        assert "Do not rely on the final assistant message alone" in observed.prompt_overlay
        assert "task.update" in observed.prompt_overlay
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_blocks_non_interactive_task_when_agent_asks_question(
    tmp_path: Path,
) -> None:
    """Detached runtime should mark tasks blocked when the agent requires human input."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_blocked.db",
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_blocked.db'}",
    )
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="ask_question",
            observed_calls=[],
        ),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Review risky deployment",
            description="Review the deployment checklist and proceed.",
            created_by_type="human",
            created_by_ref="cli",
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-b")

        assert processed is True
        updated = await service.get_task(profile_id="default", task_id=task.id)
        assert updated.status == "blocked"
        assert updated.blocked_reason_code == "task_action_ask_question"
        assert updated.blocked_reason_text == "Need human approval"
        assert updated.ready_at is None
        listed_events = await service.list_task_events(profile_id="default", task_id=task.id)
        blocked_event = next(item for item in listed_events if item.event_type == "execution_blocked")
        assert blocked_event.message == "Need human approval"
        assert blocked_event.details["blocked_reason_code"] == "task_action_ask_question"
        fallback_comment = next(item for item in listed_events if item.event_type == "comment_added")
        assert fallback_comment.message == "Blocked: Need human approval"

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed_revisit = await repo.claim_next_runnable_task(
                now_utc=datetime.now(timezone.utc) + timedelta(days=1),
                lease_until=datetime.now(timezone.utc) + timedelta(days=1, minutes=15),
                claim_token="blocked-revisit-ready",
                claimed_by="worker-b-ready",
            )
            assert claimed_revisit is None
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_preserves_human_handoff_from_running_task(
    tmp_path: Path,
) -> None:
    """Detached runtime should not overwrite a durable human handoff made during the task run."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_handoff.db",
        profile_ids=("default", "analyst"),
    )
    runtime = TaskFlowRuntimeService(
        settings=Settings(
            root_dir=tmp_path,
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_handoff.db'}",
        ),
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="handoff_human",
            observed_calls=[],
        ),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Escalate incident summary",
            description="Prepare the incident summary and route it to the on-call human reviewer.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )
        dependent = await service.create_task(
            profile_id="default",
            title="Send escalation outcome",
            description="Send the incident outcome after the AI task fully completes.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:bob",
            depends_on_task_ids=(task.id,),
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-handoff")

        assert processed is True
        updated = await service.get_task(profile_id="default", task_id=task.id)
        assert updated.status == "review"
        assert updated.owner_type == "human"
        assert updated.owner_ref == "cli_user:alice"
        assert updated.blocked_reason_code == "awaiting_human_review"
        assert updated.blocked_reason_text == "Ready for human review."
        assert updated.last_run_id is not None
        async with session_scope(factory) as session:
            task_run = await session.get(TaskRun, updated.last_run_id)
        assert task_run is not None
        assert task_run.status == "review"
        assert task_run.error_code == "awaiting_human_review"

        dependent_after = await service.get_task(profile_id="default", task_id=dependent.id)
        assert dependent_after.status == "blocked"
        assert dependent_after.blocked_reason_code == "dependency_wait"

        processed_again = await runtime.execute_next_claimable_task(worker_id="worker-handoff-2")
        assert processed_again is False
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_marks_llm_timeout_as_failed(
    tmp_path: Path,
) -> None:
    """Detached runtime should persist failed status when the LLM runtime times out."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_failed.db",
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_failed.db'}",
    )
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="llm_timeout",
            observed_calls=[],
        ),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Run long market scan",
            description="Analyze the competitive landscape and summarize the result.",
            created_by_type="human",
            created_by_ref="cli",
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-c")

        assert processed is True
        updated = await service.get_task(profile_id="default", task_id=task.id)
        assert updated.status == "failed"
        assert updated.last_error_code == "llm_timeout"
        assert updated.last_error_text == "Task run timed out while waiting for the LLM provider."
        listed_events = await service.list_task_events(profile_id="default", task_id=task.id)
        failed_event = next(item for item in listed_events if item.event_type == "execution_failed")
        assert failed_event.actor_ref == "worker-c"
        assert failed_event.details["error_code"] == "llm_timeout"
        fallback_comment = next(item for item in listed_events if item.event_type == "comment_added")
        assert fallback_comment.message == "Failed: Task run timed out while waiting for the LLM provider."
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_releases_task_when_start_transition_is_lost(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Detached runtime should release claimed work back to todo when start transition fails."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_release.db",
    )
    observed_calls: list[_ObservedCall] = []
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_release.db'}",
    )
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Retryable start transition",
            description="Verify that a lost claim during start is safely retried.",
            created_by_type="human",
            created_by_ref="cli",
        )

        async def _fail_mark_started(*, claimed):
            del claimed
            return False

        original_mark_started = runtime._mark_started
        monkeypatch.setattr(runtime, "_mark_started", _fail_mark_started)

        processed = await runtime.execute_next_claimable_task(worker_id="worker-release")

        assert processed is True
        released = await service.get_task(profile_id="default", task_id=task.id)
        assert released.status == "todo"
        assert released.started_at is None
        assert released.last_error_code == "task_claim_lost"
        assert released.last_error_text == "Failed to transition claimed task into running state"
        assert released.current_attempt == 0
        assert released.last_run_id is not None
        assert observed_calls == []

        async with session_scope(factory) as session:
            task_run = await session.get(TaskRun, released.last_run_id)
        assert task_run is not None
        assert task_run.status == "cancelled"
        assert task_run.error_code == "task_claim_lost"

        monkeypatch.setattr(runtime, "_mark_started", original_mark_started)
        processed_retry = await runtime.execute_next_claimable_task(worker_id="worker-release-retry")

        assert processed_retry is True
        completed = await service.get_task(profile_id="default", task_id=task.id)
        assert completed.status == "completed"
        assert completed.current_attempt == 1
        assert len(observed_calls) == 1
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_sweeps_expired_claims_before_reclaiming_task(
    tmp_path: Path,
) -> None:
    """Expired claims should be released and retried before a worker takes new work."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_sweep.db",
        profile_ids=("default", "analyst"),
    )
    observed_calls: list[_ObservedCall] = []
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_sweep.db'}",
    )
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Recover stale runtime claim",
            description="Recover a stale claim and finish the work.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )
        stale_now = datetime.now(timezone.utc)
        stale_session_id = f"taskflow:{task.id}"
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed = await repo.claim_next_runnable_task(
                now_utc=stale_now,
                lease_until=stale_now - timedelta(minutes=5),
                claim_token="stale-claim",
                claimed_by="taskflow-runtime:stale",
            )
            assert claimed is not None
            task_run = await repo.create_task_run(
                task_id=task.id,
                attempt=claimed.current_attempt + 1,
                owner_type=claimed.owner_type,
                owner_ref=claimed.owner_ref,
                execution_mode="detached",
                status="running",
                session_id=stale_session_id,
                run_id=None,
                worker_id="taskflow-runtime:stale",
                started_at=stale_now - timedelta(minutes=10),
            )
            attached = await repo.attach_task_run(
                task_id=task.id,
                claim_token="stale-claim",
                task_run_id=task_run.id,
                session_id=stale_session_id,
            )
            assert attached is True
            started = await repo.mark_task_started(
                task_id=task.id,
                claim_token="stale-claim",
                started_at=stale_now - timedelta(minutes=10),
            )
            assert started is True

        processed = await runtime.execute_next_claimable_task(worker_id="worker-sweep")

        assert processed is True
        updated = await service.get_task(profile_id="default", task_id=task.id)
        assert updated.status == "completed"
        assert updated.current_attempt == 2
        task_runs = await service.list_task_runs(profile_id="default", task_id=task.id)
        assert len(task_runs) == 2
        stale_run = next(item for item in task_runs if item.worker_id == "taskflow-runtime:stale")
        fresh_run = next(item for item in task_runs if item.worker_id == "worker-sweep")
        assert stale_run.status == "cancelled"
        assert stale_run.error_code == "task_lease_expired"
        assert fresh_run.status == "completed"
        events = await service.list_task_events(profile_id="default", task_id=task.id)
        assert any(item.event_type == "execution_completed" for item in events)
        assert {item.event_type for item in events} >= {"created", "lease_expired", "execution_completed"}
        fallback_comment = next(item for item in events if item.event_type == "comment_added")
        assert fallback_comment.message == "Completed: analysis complete"
        assert len(observed_calls) == 1
        assert observed_calls[0].session_id == f"taskflow:{task.id}"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_sweep_can_be_scoped_to_profile(tmp_path: Path) -> None:
    """Manual stale-claim maintenance should only repair work inside the selected profile."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_profile_sweep.db",
        profile_ids=("default", "ops"),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_profile_sweep.db'}",
    )
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=[],
        ),
    )
    service = TaskFlowService(factory)
    try:
        default_task = await service.create_task(
            profile_id="default",
            title="Default stale task",
            description="Repair the default-profile stale task.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
        )
        ops_task = await service.create_task(
            profile_id="ops",
            title="Ops stale task",
            description="Leave the ops-profile stale task untouched for now.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="ops",
        )
        stale_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            for task_id, claim_token, claimed_by in (
                (default_task.id, "stale-default-claim", "taskflow-runtime:default"),
                (ops_task.id, "stale-ops-claim", "taskflow-runtime:ops"),
            ):
                claimed = await repo.claim_next_runnable_task(
                    now_utc=stale_now,
                    lease_until=stale_now - timedelta(minutes=5),
                    claim_token=claim_token,
                    claimed_by=claimed_by,
                )
                assert claimed is not None
                task_run = await repo.create_task_run(
                    task_id=task_id,
                    attempt=claimed.current_attempt + 1,
                    owner_type=claimed.owner_type,
                    owner_ref=claimed.owner_ref,
                    execution_mode="detached",
                    status="running",
                    session_id=f"taskflow:{task_id}",
                    run_id=None,
                    worker_id=claimed_by,
                    started_at=stale_now - timedelta(minutes=10),
                )
                attached = await repo.attach_task_run(
                    task_id=task_id,
                    claim_token=claim_token,
                    task_run_id=task_run.id,
                    session_id=f"taskflow:{task_id}",
                )
                assert attached is True
                started = await repo.mark_task_started(
                    task_id=task_id,
                    claim_token=claim_token,
                    started_at=stale_now - timedelta(minutes=10),
                )
                assert started is True

        released_count = await runtime.sweep_expired_claims(
            worker_id="taskflow-cli-maintenance",
            profile_id="default",
            limit=10,
        )

        assert released_count == 1
        default_after = await service.get_task(profile_id="default", task_id=default_task.id)
        ops_after = await service.get_task(profile_id="ops", task_id=ops_task.id)
        assert default_after.status == "todo"
        assert default_after.last_error_code == "task_lease_expired"
        assert ops_after.status == "running"

        default_events = await service.list_task_events(profile_id="default", task_id=default_task.id)
        ops_events = await service.list_task_events(profile_id="ops", task_id=ops_task.id)
        assert default_events[0].event_type == "lease_expired"
        assert all(item.event_type != "lease_expired" for item in ops_events)
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_sweep_reinstalls_active_owner_index_when_duplicates_clear(
    tmp_path: Path,
) -> None:
    """Maintenance sweep should recreate the active-owner index once duplicate stale claims are repaired."""

    db_name = "taskflow_runtime_reinstall_index.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
    )
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=[],
        ),
    )
    service = TaskFlowService(factory)
    try:
        first = await service.create_task(
            profile_id="default",
            title="First stale duplicate",
            description="Leave this duplicate stale until maintenance repairs it.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )
        second = await service.create_task(
            profile_id="default",
            title="Second stale duplicate",
            description="Also leave this duplicate stale until maintenance repairs it.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )

        async with engine.begin() as conn:
            await conn.execute(text("DROP INDEX ux_task_active_ai_owner"))
            await conn.execute(
                text(
                    "UPDATE task "
                    "SET status = 'claimed', "
                    "    claim_token = 'dup-1', "
                    "    claimed_by = 'taskflow-runtime:dup-1', "
                    "    lease_until = :lease_until "
                    "WHERE id = :task_id"
                ),
                {
                    "task_id": first.id,
                    "lease_until": datetime.now(timezone.utc) - timedelta(minutes=5),
                },
            )
            await conn.execute(
                text(
                    "UPDATE task "
                    "SET status = 'claimed', "
                    "    claim_token = 'dup-2', "
                    "    claimed_by = 'taskflow-runtime:dup-2', "
                    "    lease_until = :lease_until "
                    "WHERE id = :task_id"
                ),
                {
                    "task_id": second.id,
                    "lease_until": datetime.now(timezone.utc) - timedelta(minutes=5),
                },
            )

        released_count = await runtime.sweep_expired_claims(
            worker_id="taskflow-maintenance",
            limit=10,
        )

        assert released_count == 2
        async with engine.connect() as conn:
            rows = (await conn.execute(text("PRAGMA index_list(task)"))).all()
        index_names = {str(row[1]) for row in rows}
        assert "ux_task_active_ai_owner" in index_names
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_respects_optional_owner_ref_filter(tmp_path: Path) -> None:
    """Detached runtime owner_ref filter should only apply when explicitly configured."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_owner_ref_filter.db",
        profile_ids=("default", "researcher", "analyst"),
    )
    service = TaskFlowService(factory)
    try:
        observed_calls: list[_ObservedCall] = []
        filtered_runtime = TaskFlowRuntimeService(
            session_factory=factory,
            session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
                session,
                behavior="complete",
                observed_calls=observed_calls,
            ),
            settings=Settings(taskflow_runtime_owner_ref="researcher", llm_max_iterations=10),
        )
        try:
            allowed = await service.create_task(
                profile_id="default",
                title="Allowed owner",
                description="Please handle researcher queue.",
                owner_type="ai_profile",
                owner_ref="researcher",
                created_by_type="human",
                created_by_ref="cli",
            )
            skipped = await service.create_task(
                profile_id="default",
                title="Skipped owner",
                description="Please handle analyst queue.",
                owner_type="ai_profile",
                owner_ref="analyst",
                created_by_type="human",
                created_by_ref="cli",
            )

            processed = await filtered_runtime.execute_next_claimable_task(
                worker_id="taskflow-runtime:owner-filtered"
            )

            assert processed is True
            allowed_after = await service.get_task(profile_id="default", task_id=allowed.id)
            skipped_after = await service.get_task(profile_id="default", task_id=skipped.id)
            assert observed_calls[0].task_id == allowed.id
            assert allowed_after.status == "completed"
            assert skipped_after.status == "todo"
        finally:
            await filtered_runtime.shutdown()

        unfiltered_runtime = TaskFlowRuntimeService(
            session_factory=factory,
            session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
                session,
                behavior="complete",
                observed_calls=observed_calls,
            ),
            settings=Settings(llm_max_iterations=10),
        )
        try:
            processed = await unfiltered_runtime.execute_next_claimable_task(
                worker_id="taskflow-runtime:owner-unfiltered"
            )
            assert processed is True
            skipped_after_unfiltered = await service.get_task(profile_id="default", task_id=skipped.id)
            assert observed_calls[1].task_id == skipped.id
            assert skipped_after_unfiltered.status == "completed"
        finally:
            await unfiltered_runtime.shutdown()
    finally:
        await engine.dispose()


async def test_taskflow_runtime_claims_only_one_active_task_per_ai_profile(
    tmp_path: Path,
) -> None:
    """Detached scheduling should never hand one agent multiple active tasks at once."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_per_agent_limit.db",
        profile_ids=("default", "analyst", "papercliper"),
    )
    service = TaskFlowService(factory)
    try:
        analyst_first = await service.create_task(
            profile_id="default",
            title="Analyst high priority",
            description="Take the highest-priority analyst task first.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
            priority=90,
        )
        analyst_second = await service.create_task(
            profile_id="default",
            title="Analyst second task",
            description="This should wait until analyst is free again.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
            priority=80,
        )
        papercliper_task = await service.create_task(
            profile_id="default",
            title="Papercliper task",
            description="Take this once the analyst already has active work.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="papercliper",
            priority=70,
        )

        claim_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            first_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-analyst-1",
                claimed_by="taskflow-runtime:0",
            )
            second_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-papercliper-1",
                claimed_by="taskflow-runtime:1",
            )
            third_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-none-left",
                claimed_by="taskflow-runtime:2",
            )

        assert first_claim is not None
        assert first_claim.id == analyst_first.id
        assert second_claim is not None
        assert second_claim.id == papercliper_task.id
        assert third_claim is None

        waiting = await service.get_task(profile_id="default", task_id=analyst_second.id)
        assert waiting.status == "todo"
    finally:
        await engine.dispose()


async def test_taskflow_runtime_allows_same_ai_owner_ref_across_profiles(
    tmp_path: Path,
) -> None:
    """Claim guard should stay profile-scoped for shared AI owner refs."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_cross_profile_owner_ref.db",
        profile_ids=("default", "researcher", "analyst"),
    )
    service = TaskFlowService(factory)
    try:
        default_task = await service.create_task(
            profile_id="default",
            title="Default analyst claim",
            description="Claim analyst work in default profile.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
            priority=90,
        )
        researcher_task = await service.create_task(
            profile_id="researcher",
            title="Researcher analyst claim",
            description="Claim analyst work in researcher profile.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
            priority=85,
        )

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            first_claim = await repo.claim_next_runnable_task(
                now_utc=datetime.now(timezone.utc),
                lease_until=datetime.now(timezone.utc) + timedelta(minutes=15),
                claim_token="cross-profile-claim-default",
                claimed_by="taskflow-runtime:worker-default",
            )
            second_claim = await repo.claim_next_runnable_task(
                now_utc=datetime.now(timezone.utc),
                lease_until=datetime.now(timezone.utc) + timedelta(minutes=15),
                claim_token="cross-profile-claim-researcher",
                claimed_by="taskflow-runtime:worker-researcher",
            )

        assert first_claim is not None
        assert second_claim is not None
        assert {first_claim.id, second_claim.id} == {default_task.id, researcher_task.id}
        assert first_claim.profile_id != second_claim.profile_id
    finally:
        await engine.dispose()


async def test_taskflow_runtime_spreads_equal_priority_claims_across_flows(
    tmp_path: Path,
) -> None:
    """Equal-priority claims should prefer an idle flow over piling onto one already-active flow."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_flow_spread.db",
        profile_ids=("default", "analyst", "researcher", "papercliper"),
    )
    service = TaskFlowService(factory)
    try:
        flow_a = await service.create_flow(
            profile_id="default",
            title="Flow A",
            description="First launch stream.",
            created_by_type="human",
            created_by_ref="cli",
        )
        flow_b = await service.create_flow(
            profile_id="default",
            title="Flow B",
            description="Second launch stream.",
            created_by_type="human",
            created_by_ref="cli",
        )
        flow_a_first = await service.create_task(
            profile_id="default",
            flow_id=flow_a.id,
            title="Flow A analyst",
            description="Take the first Flow A task.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
            priority=90,
        )
        flow_a_second = await service.create_task(
            profile_id="default",
            flow_id=flow_a.id,
            title="Flow A researcher",
            description="Take the second Flow A task.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="researcher",
            priority=90,
        )
        flow_b_first = await service.create_task(
            profile_id="default",
            flow_id=flow_b.id,
            title="Flow B papercliper",
            description="Take the first Flow B task.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="papercliper",
            priority=90,
        )

        claim_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            first_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-flow-a-1",
                claimed_by="taskflow-runtime:0",
            )
            second_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-flow-b-1",
                claimed_by="taskflow-runtime:1",
            )

        assert first_claim is not None
        assert first_claim.id == flow_a_first.id
        assert second_claim is not None
        assert second_claim.id == flow_b_first.id
        waiting = await service.get_task(profile_id="default", task_id=flow_a_second.id)
        assert waiting.status == "todo"
    finally:
        await engine.dispose()


async def test_taskflow_runtime_keeps_priority_ahead_of_flow_fairness(
    tmp_path: Path,
) -> None:
    """Higher-priority work should still win even when another flow is currently idle."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_flow_priority.db",
        profile_ids=("default", "analyst", "researcher", "papercliper"),
    )
    service = TaskFlowService(factory)
    try:
        flow_a = await service.create_flow(
            profile_id="default",
            title="Priority Flow A",
            description="Higher-priority stream.",
            created_by_type="human",
            created_by_ref="cli",
        )
        flow_b = await service.create_flow(
            profile_id="default",
            title="Priority Flow B",
            description="Lower-priority stream.",
            created_by_type="human",
            created_by_ref="cli",
        )
        flow_a_first = await service.create_task(
            profile_id="default",
            flow_id=flow_a.id,
            title="Flow A first",
            description="Take the highest-priority task first.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
            priority=95,
        )
        flow_a_second = await service.create_task(
            profile_id="default",
            flow_id=flow_a.id,
            title="Flow A second",
            description="This is still higher priority than Flow B.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="researcher",
            priority=94,
        )
        flow_b_first = await service.create_task(
            profile_id="default",
            flow_id=flow_b.id,
            title="Flow B first",
            description="This should wait behind the higher-priority Flow A task.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="papercliper",
            priority=90,
        )

        claim_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            first_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-priority-a-1",
                claimed_by="taskflow-runtime:0",
            )
            second_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-priority-a-2",
                claimed_by="taskflow-runtime:1",
            )

        assert first_claim is not None
        assert first_claim.id == flow_a_first.id
        assert second_claim is not None
        assert second_claim.id == flow_a_second.id
        waiting = await service.get_task(profile_id="default", task_id=flow_b_first.id)
        assert waiting.status == "todo"
    finally:
        await engine.dispose()


async def test_taskflow_runtime_treats_no_flow_backlog_as_its_own_fairness_bucket(
    tmp_path: Path,
) -> None:
    """Equal-priority no-flow work should not bypass flow spreading once it already has active load."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_no_flow_bucket.db",
        profile_ids=("default", "analyst", "researcher", "papercliper"),
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Flow bucket",
            description="Track fairness against no-flow backlog.",
            created_by_type="human",
            created_by_ref="cli",
        )
        no_flow_first = await service.create_task(
            profile_id="default",
            title="No-flow analyst",
            description="Take the first no-flow task.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
            priority=90,
        )
        no_flow_second = await service.create_task(
            profile_id="default",
            title="No-flow researcher",
            description="This should wait behind the idle flow bucket.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="researcher",
            priority=90,
        )
        flow_task = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Flow papercliper",
            description="Idle flow work should be preferred on the second claim.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="papercliper",
            priority=90,
        )

        claim_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            first_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-no-flow-1",
                claimed_by="taskflow-runtime:0",
            )
            second_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-flow-after-no-flow",
                claimed_by="taskflow-runtime:1",
            )

        assert first_claim is not None
        assert first_claim.id == no_flow_first.id
        assert second_claim is not None
        assert second_claim.id == flow_task.id

        waiting = await service.get_task(profile_id="default", task_id=no_flow_second.id)
        assert waiting.status == "todo"
    finally:
        await engine.dispose()


async def test_taskflow_runtime_retries_claim_after_active_owner_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Detached runtime should retry when a transient active-owner uniqueness conflict happens."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_claim_retry.db",
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory)
    observed_calls: list[_ObservedCall] = []
    runtime = TaskFlowRuntimeService(
        settings=Settings(
            root_dir=tmp_path,
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_claim_retry.db'}",
        ),
        session_factory=factory,
        engine=engine,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
    )
    original_claim_next = TaskFlowRepository.claim_next_runnable_task
    claim_attempts = 0

    async def _flaky_claim_next(self, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal claim_attempts
        claim_attempts += 1
        if claim_attempts == 1:
            raise IntegrityError(
                statement="UPDATE task SET status='claimed' /* ux_task_active_ai_owner */",
                params=None,
                orig=Exception("UNIQUE constraint failed: task.profile_id, task.owner_ref (ux_task_active_ai_owner)"),
            )
        return await original_claim_next(self, **kwargs)

    monkeypatch.setattr(TaskFlowRepository, "claim_next_runnable_task", _flaky_claim_next)
    try:
        await service.create_task(
            profile_id="default",
            title="Retry transient claim conflict",
            description="Claim this after retrying a transient uniqueness conflict.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
            priority=90,
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-retry-claim")

        assert processed is True
        assert claim_attempts >= 2
        task = (await service.list_tasks(profile_id="default"))[0]
        assert task.owner_ref == "analyst"
        assert task.status == "completed"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_keeps_dependency_wait_tasks_out_of_timer_retries(
    tmp_path: Path,
) -> None:
    """Dependency-wait blockers should wake only from dependency reconciliation, not timed retries."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_dependency_wait.db",
        profile_ids=("default", "analyst"),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_dependency_wait.db'}",
    )
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="dependency_wait",
            observed_calls=[],
        ),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Wait for delegated work",
            description="Delegate a prerequisite and wait on dependency completion.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-dependency")

        assert processed is True
        updated = await service.get_task(profile_id="default", task_id=task.id)
        assert updated.status == "blocked"
        assert updated.blocked_reason_code == "dependency_wait"
        assert updated.ready_at is None

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed = await repo.claim_next_runnable_task(
                now_utc=datetime.now(timezone.utc) + timedelta(days=1),
                lease_until=datetime.now(timezone.utc) + timedelta(days=1, minutes=15),
                claim_token="dependency-wait-late-claim",
                claimed_by="taskflow-runtime:late",
            )

        assert claimed is None
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_skips_plan_tasks_when_claiming_work(
    tmp_path: Path,
) -> None:
    """Detached runtime should auto-block misassigned PLAN tasks and continue with runnable work."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_plan_skip.db",
        profile_ids=("default", "researcher", "analyst"),
    )
    observed_calls: list[_ObservedCall] = []
    service = TaskFlowService(factory)
    runtime = TaskFlowRuntimeService(
        session_factory=factory,
        session_runner_factory=lambda session_factory, _profile_id: _FakeSessionRunner(
            session_factory,
            behavior="complete",
            observed_calls=observed_calls,
        ),
        settings=Settings(taskflow_runtime_owner_ref="researcher"),
    )
    try:
        planned = await service.create_task(
            profile_id="default",
            title="Draft the task before AI starts",
            description="Stay in PLAN until a human finishes the brief.",
            status="plan",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="operator",
        )
        async with session_scope(factory) as session:
            await session.execute(
                text(
                    "UPDATE task SET status = 'plan', owner_type = 'ai_profile', owner_ref = 'researcher' "
                    "WHERE profile_id = :profile_id AND id = :task_id"
                ),
                {"profile_id": "default", "task_id": planned.id},
            )
            await session.commit()
        runnable = await service.create_task(
            profile_id="default",
            title="Run once planning is complete",
            description="This task is ready for the detached runtime.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="researcher",
        )

        untouched = await service.create_task(
            profile_id="default",
            title="Other owner remains queued",
            description="Wait for another runtime worker",
            owner_type="ai_profile",
            owner_ref="analyst",
            created_by_type="human",
            created_by_ref="cli",
        )

        processed = await runtime.execute_next_claimable_task(worker_id="taskflow-runtime:plan-skip")

        assert processed is True
        planned_after = await service.get_task(profile_id="default", task_id=planned.id)
        runnable_after = await service.get_task(profile_id="default", task_id=runnable.id)
        untouched_after = await service.get_task(profile_id="default", task_id=untouched.id)
        assert planned_after.status == "blocked"
        assert planned_after.blocked_reason_code == "invalid_plan_status"
        assert planned_after.blocked_reason_text is not None
        assert "PLAN" in planned_after.blocked_reason_text
        assert planned_after.last_error_code is None
        assert planned_after.last_error_text is None
        assert runnable_after.status == "completed"
        assert untouched_after.status == "todo"
        assert observed_calls[0].task_id == runnable.id

        events = await service.list_task_events(profile_id="default", task_id=planned.id)
        event_types = [event.event_type for event in events]
        assert event_types[0] == "runtime_claim_rejected"
        assert "blocked" in event_types
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_includes_task_attachments_in_execution_message(
    tmp_path: Path,
) -> None:
    """Detached runtime should surface persisted task attachments to the worker turn."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_attachments.db",
        profile_ids=("default",),
    )
    observed_calls: list[_ObservedCall] = []
    service = TaskFlowService(factory)
    runtime = TaskFlowRuntimeService(
        session_factory=factory,
        session_runner_factory=lambda session_factory, _profile_id: _FakeSessionRunner(
            session_factory,
            behavior="complete",
            observed_calls=observed_calls,
        ),
    )
    try:
        task = await service.create_task(
            profile_id="default",
            title="Review the attached brief",
            description="Use the attached requirements before answering.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            attachments=(
                {
                    "name": "brief.txt",
                    "content_type": "text/plain",
                    "content_base64": "VXNlciBuZWVkczogc2hpcCB0aGUgZGVzY3JpcHRpb24gbWlncmF0aW9uLg==",
                    "kind": "text",
                },
            ),
        )

        processed = await runtime.execute_next_claimable_task(worker_id="taskflow-runtime:attachments")

        assert processed is True
        assert observed_calls[0].task_id == task.id
        assert "Task Attachments:" in observed_calls[0].message
        assert "brief.txt" in observed_calls[0].message
        assert "User needs: ship the description migration." in observed_calls[0].message
    finally:
        await runtime.shutdown()
        await engine.dispose()


def test_taskflow_context_overrides_include_runtime_task_guidance() -> None:
    """Task Flow prompt overlay should teach decomposition and human handoff rules."""

    overrides = build_task_flow_context_overrides(
        task_id="task_demo",
        task_profile_id="default",
        owner_type="ai_profile",
        owner_ref="analyst",
        flow_id="flow_demo",
        source_type="manual",
        source_ref="source_demo",
        priority=75,
        attempt=2,
        requires_review=True,
        labels=("ops", "launch"),
    )

    assert isinstance(overrides.runtime_metadata, dict)
    taskflow_payload = overrides.runtime_metadata.get("taskflow")
    assert isinstance(taskflow_payload, dict)
    assert taskflow_payload["task_id"] == "task_demo"
    assert taskflow_payload["task_profile_id"] == "default"
    assert overrides.execution_planning_mode == "on"
    assert overrides.prompt_overlay is not None
    assert "This runtime is non-interactive." in overrides.prompt_overlay
    assert "task.update" in overrides.prompt_overlay
    assert "task.block" in overrides.prompt_overlay
    assert "task.flow.create" in overrides.prompt_overlay
    assert "task.comment.add" in overrides.prompt_overlay
    assert "task.delegate" in overrides.prompt_overlay
    assert "execution plan" in overrides.prompt_overlay
    assert "another ai_profile agent" in overrides.prompt_overlay
    assert "task.dependency.add" in overrides.prompt_overlay
    assert "retry_after_sec" in overrides.prompt_overlay
