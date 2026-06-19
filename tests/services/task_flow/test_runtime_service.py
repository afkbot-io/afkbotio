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
from afkbot.services.task_flow.work_modes import resolve_task_work_mode
from afkbot.settings import Settings
from tests.repositories._harness import build_repository_factory


def _write_profile_subagent(
    *,
    settings: Settings,
    profile_id: str,
    subagent_name: str,
    markdown: str,
) -> None:
    path = settings.profiles_dir / profile_id / "subagents" / f"{subagent_name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _write_team_runtime_config(
    *,
    settings: Settings,
    profile_id: str,
    team_profile_ids: tuple[str, ...],
) -> None:
    _ = (settings, profile_id, team_profile_ids)


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
                    str(metadata.get("transport") or "").strip()
                    if isinstance(metadata, dict)
                    else None
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
                payload={"assistant_message": "Need operator approval"},
            )
            return TurnResult(
                run_id=run.id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(
                    action="ask_question",
                    message="Need operator approval",
                    question_id="q-review",
                ),
            )
        if self._behavior == "handoff_employee":
            assert isinstance(taskflow_payload, dict)
            updated = await TaskFlowRepository(self._session).update_task(
                profile_id=str(taskflow_payload.get("task_profile_id") or ""),
                task_id=str(taskflow_payload.get("task_id") or ""),
                owner_type="employee",
                owner_ref="reviewer",
                status="review",
                blocked_reason_code="awaiting_employee_review",
                blocked_reason_text="Ready for employee review.",
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
        if self._behavior == "approve_review":
            assert isinstance(taskflow_payload, dict)
            updated = await TaskFlowRepository(self._session).update_task(
                profile_id=str(taskflow_payload.get("task_profile_id") or ""),
                task_id=str(taskflow_payload.get("task_id") or ""),
                status="completed",
                blocked_reason_code=None,
                blocked_reason_text=None,
            )
            assert updated is not None
            await runlog.create_event(
                run_id=run.id,
                session_id=session_id,
                event_type="turn.finalize",
                payload={
                    "assistant_message": "Review approved.",
                    "blocked_reason": None,
                    "state": "finalized",
                },
            )
            return TurnResult(
                run_id=run.id,
                session_id=session_id,
                profile_id=profile_id,
                envelope=ActionEnvelope(action="finalize", message="Review approved."),
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


async def test_taskflow_runtime_executes_employee_owned_task_and_unblocks_dependents(
    tmp_path: Path,
) -> None:
    """Detached runtime should complete employee-owned work and unblock dependent tasks."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_complete.db",
        profile_ids=("default", "analyst"),
    )
    observed_calls: list[_ObservedCall] = []
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_complete.db'}",
        taskflow_knowledge_maintenance_enabled=False,
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
        flow = await service.create_flow(
            profile_id="default",
            title="Support operations",
            description="Coordinate support analysis with durable docs.",
            created_by_type="human",
            created_by_ref="cli",
        )
        first = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Analyze support backlog",
            description="Summarize the last ten support tickets.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="papercliper",
        )
        await service.put_task_document(
            profile_id="default",
            task_id=first.id,
            document_key="handoff",
            title="Analysis handoff",
            body="Read tickets, group themes, and report blockers.",
            actor_type="human",
            actor_ref="cli",
        )
        dependent = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Send triage summary",
            description="Send the triage summary after analysis is ready.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
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
        execution_event = next(
            item for item in listed_events if item.event_type == "execution_completed"
        )
        assert execution_event.actor_type == "runtime"
        assert execution_event.actor_ref == "worker-a"
        assert execution_event.to_status == "completed"
        assert execution_event.details["run_id"] == listed_runs[0].run_id
        assert any(item.event_type == "comment_added" for item in listed_events)
        fallback_comment = next(
            item for item in listed_events if item.event_type == "comment_added"
        )
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
        assert observed.profile_id == "default"
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
        assert "Task Flow Context Bundle:" in observed.message
        assert "flow: Support operations" in observed.message
        assert "knowledge packet:" in observed.message
        assert "task.handoff r1 [draft] Analysis handoff" in observed.message
        assert "task docs:" in observed.message
        assert "handoff r1: Analysis handoff" in observed.message
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_blocks_non_interactive_task_when_agent_asks_question(
    tmp_path: Path,
) -> None:
    """Detached runtime should mark tasks blocked when the agent requires operator input."""

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
        assert updated.blocked_reason_text == "Need operator approval"
        assert updated.ready_at is None
        listed_events = await service.list_task_events(profile_id="default", task_id=task.id)
        blocked_event = next(
            item for item in listed_events if item.event_type == "execution_blocked"
        )
        assert blocked_event.message == "Need operator approval"
        assert blocked_event.details["blocked_reason_code"] == "task_action_ask_question"
        fallback_comment = next(
            item for item in listed_events if item.event_type == "comment_added"
        )
        assert fallback_comment.message == "Blocked: Need operator approval"

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


async def test_taskflow_runtime_preserves_employee_handoff_from_running_task(
    tmp_path: Path,
) -> None:
    """Detached runtime should not overwrite a durable employee handoff made during the task run."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_handoff.db",
        profile_ids=("default", "analyst"),
    )
    runtime = TaskFlowRuntimeService(
        settings=Settings(
            root_dir=tmp_path,
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_handoff.db'}",
            taskflow_runtime_owner_ref="papercliper",
        ),
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="handoff_employee",
            observed_calls=[],
        ),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Escalate incident summary",
            description="Prepare the incident summary and route it to the reviewer employee.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="papercliper",
        )
        dependent = await service.create_task(
            profile_id="default",
            title="Send escalation outcome",
            description="Send the incident outcome after the Employee task fully completes.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            depends_on_task_ids=(task.id,),
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-handoff")

        assert processed is True
        updated = await service.get_task(profile_id="default", task_id=task.id)
        assert updated.status == "review"
        assert updated.owner_type == "employee"
        assert updated.owner_ref == "reviewer"
        assert updated.blocked_reason_code == "awaiting_employee_review"
        assert updated.blocked_reason_text == "Ready for employee review."
        assert updated.last_run_id is not None
        async with session_scope(factory) as session:
            task_run = await session.get(TaskRun, updated.last_run_id)
        assert task_run is not None
        assert task_run.status == "review"
        assert task_run.error_code == "awaiting_employee_review"

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
        fallback_comment = next(
            item for item in listed_events if item.event_type == "comment_added"
        )
        assert (
            fallback_comment.message
            == "Failed: Task run timed out while waiting for the LLM provider."
        )
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
            owner_type="employee",
            owner_ref="papercliper",
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
        processed_retry = await runtime.execute_next_claimable_task(
            worker_id="worker-release-retry"
        )

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
            owner_type="employee",
            owner_ref="papercliper",
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
        assert {item.event_type for item in events} >= {
            "created",
            "lease_expired",
            "recovery_action_created",
            "execution_completed",
        }
        fallback_comment = next(item for item in events if item.event_type == "comment_added")
        assert fallback_comment.message == "Completed: analysis complete"
        assert len(observed_calls) == 1
        assert observed_calls[0].session_id == f"taskflow:{task.id}"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_throttles_background_maintenance_between_claim_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim loop should not sweep expired claims on every rapid polling pass."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_maintenance_throttle.db",
        profile_ids=("default",),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_maintenance_throttle.db'}",
        taskflow_runtime_poll_interval_sec=5.0,
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
    maintenance_calls: list[str] = []

    async def _fake_sweep(
        *,
        worker_id: str,
        limit: int = 25,
        profile_id: str | None = None,
        owner_ref: str | None = None,
    ) -> int:
        _ = limit, profile_id, owner_ref
        maintenance_calls.append(worker_id)
        return 0

    monkeypatch.setattr(runtime, "sweep_expired_claims", _fake_sweep)

    try:
        first = await runtime.execute_next_claimable_task(worker_id="worker-throttle")
        second = await runtime.execute_next_claimable_task(worker_id="worker-throttle")
    finally:
        await runtime.shutdown()
        await engine.dispose()

    assert first is False
    assert second is False
    assert maintenance_calls == ["worker-throttle"]


async def test_taskflow_runtime_throttle_ignores_disabled_knowledge_maintenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled knowledge maintenance should not bypass the shared maintenance throttle."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_disabled_knowledge_throttle.db",
        profile_ids=("default",),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_disabled_knowledge_throttle.db'}",
        taskflow_runtime_poll_interval_sec=5.0,
        taskflow_knowledge_maintenance_enabled=False,
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

    class _CountingMaintenanceLock:
        entries = 0

        async def __aenter__(self) -> "_CountingMaintenanceLock":
            self.entries += 1
            return self

        async def __aexit__(
            self,
            exc_type: object,
            exc: object,
            traceback: object,
        ) -> None:
            _ = exc_type, exc, traceback

    lock = _CountingMaintenanceLock()

    async def _fake_sweep(
        *,
        worker_id: str,
        limit: int = 25,
        profile_id: str | None = None,
        owner_ref: str | None = None,
    ) -> int:
        _ = worker_id, limit, profile_id, owner_ref
        return 0

    async def _fake_prune(*, worker_id: str) -> None:
        _ = worker_id

    monkeypatch.setattr(runtime, "_maintenance_lock", lock)
    monkeypatch.setattr(runtime, "sweep_expired_claims", _fake_sweep)
    monkeypatch.setattr(runtime, "_prune_runtime_history", _fake_prune)

    try:
        await runtime._maybe_run_maintenance(worker_id="worker-disabled-knowledge")
        await runtime._maybe_run_maintenance(worker_id="worker-disabled-knowledge")
    finally:
        await runtime.shutdown()
        await engine.dispose()

    assert lock.entries == 1


async def test_taskflow_runtime_creates_and_claims_knowledge_maintenance_task(
    tmp_path: Path,
) -> None:
    """Runtime maintenance should create CTO knowledge work without generic automation prompts."""

    db_name = "taskflow_runtime_knowledge_maintenance.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        taskflow_runtime_poll_interval_sec=5.0,
        taskflow_knowledge_maintenance_interval_sec=60,
        taskflow_public_principal_required=False,
    )
    service = TaskFlowService(factory, settings=settings, engine=engine)
    observed_calls: list[_ObservedCall] = []
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
    )
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Runtime knowledge maintenance",
            description="Runtime should create a CTO maintenance task.",
            created_by_type="human",
            created_by_ref="cli",
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-knowledge")

        assert processed is True
        assert len(observed_calls) == 1
        assert observed_calls[0].prompt_overlay is not None
        assert "work_mode: knowledge_maintenance" in observed_calls[0].prompt_overlay
        assert "Knowledge maintenance work mode." in observed_calls[0].prompt_overlay
        async with session_scope(factory) as session:
            tasks = await TaskFlowRepository(session).list_tasks_by_source(
                profile_id="default",
                source_type="knowledge_maintenance",
                source_ref=f"flow:{flow.id}",
            )
        assert len(tasks) == 1
        assert tasks[0].owner_type == "employee"
        assert tasks[0].owner_ref == "default"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_blocks_manager_intake_autocomplete_without_delegation(
    tmp_path: Path,
) -> None:
    """Detached auto-finalize should not complete manager intake work directly."""

    db_name = "taskflow_runtime_manager_intake_guard.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        taskflow_public_principal_required=False,
    )
    service = TaskFlowService(factory, settings=settings, engine=engine)
    observed_calls: list[_ObservedCall] = []
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
    )
    try:
        task = await service.create_task(
            profile_id="default",
            title="Manager intake runtime",
            description="Runtime must delegate before completion.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            labels=("manager-intake",),
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-manager-intake")

        assert processed is True
        async with session_scope(factory) as session:
            row = await TaskFlowRepository(session).get_task(
                profile_id="default",
                task_id=task.id,
            )
        assert row is not None
        assert row.status == "blocked"
        assert row.blocked_reason_code == "manager_intake_delegation_required"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_throttles_runtime_history_pruning_between_claim_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claim loop should run bounded history pruning at most once per prune cadence."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_history_maintenance_throttle.db",
        profile_ids=("default",),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=(
            f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_history_maintenance_throttle.db'}"
        ),
        taskflow_runtime_poll_interval_sec=5.0,
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
    maintenance_calls: list[str] = []
    prune_calls: list[dict[str, object]] = []

    async def _fake_sweep(
        *,
        worker_id: str,
        limit: int = 25,
        profile_id: str | None = None,
        owner_ref: str | None = None,
    ) -> int:
        _ = limit, profile_id, owner_ref
        maintenance_calls.append(worker_id)
        return 0

    monkeypatch.setattr(runtime, "sweep_expired_claims", _fake_sweep)

    async def _fake_prune_runtime_history(
        engine,
        *,
        task_event_before=None,
        task_run_before=None,
        runlog_event_before=None,
        batch_size: int = 500,
    ):
        prune_calls.append(
            {
                "engine": engine,
                "task_event_before": task_event_before,
                "task_run_before": task_run_before,
                "runlog_event_before": runlog_event_before,
                "batch_size": batch_size,
            }
        )
        from afkbot.db.bootstrap_runtime import RuntimeHistoryPruneResult

        return RuntimeHistoryPruneResult()

    monkeypatch.setattr(
        "afkbot.services.task_flow.runtime_service.prune_runtime_history",
        _fake_prune_runtime_history,
    )

    try:
        first = await runtime.execute_next_claimable_task(worker_id="worker-history-throttle")
        second = await runtime.execute_next_claimable_task(worker_id="worker-history-throttle")
    finally:
        await runtime.shutdown()
        await engine.dispose()

    assert first is False
    assert second is False
    assert maintenance_calls == ["worker-history-throttle"]
    assert len(prune_calls) == 1
    assert prune_calls[0]["engine"] is engine
    assert prune_calls[0]["task_event_before"] is not None
    assert prune_calls[0]["batch_size"] == settings.taskflow_runtime_maintenance_batch_size
    assert prune_calls[0]["task_run_before"] is not None
    assert prune_calls[0]["runlog_event_before"] is not None


async def test_taskflow_runtime_logs_runtime_history_prune_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Runtime prune logging should report task_run_count in the task_runs field."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_history_prune_logging.db",
        profile_ids=("default",),
    )
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_history_prune_logging.db'}",
        taskflow_runtime_maintenance_batch_size=17,
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

    async def _fake_prune_runtime_history(
        prune_engine,
        *,
        task_event_before=None,
        task_run_before=None,
        runlog_event_before=None,
        batch_size: int = 500,
    ):
        _ = task_event_before, task_run_before, runlog_event_before, batch_size
        assert prune_engine is engine
        from afkbot.db.bootstrap_runtime import RuntimeHistoryPruneResult

        return RuntimeHistoryPruneResult(task_event_count=5, task_run_count=2, runlog_event_count=3)

    monkeypatch.setattr(
        "afkbot.services.task_flow.runtime_service.prune_runtime_history",
        _fake_prune_runtime_history,
    )

    try:
        caplog.set_level("INFO", logger="afkbot.services.task_flow.runtime_service")
        await runtime._prune_runtime_history(worker_id="worker-log")
    finally:
        await runtime.shutdown()
        await engine.dispose()

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "INFO"
    assert record.message == (
        "taskflow_runtime_pruned_history worker_id=worker-log task_runs=2 runlog_events=3"
    )


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
            owner_type="employee",
            owner_ref="default",
        )
        ops_task = await service.create_task(
            profile_id="ops",
            title="Ops stale task",
            description="Leave the ops-profile stale task untouched for now.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
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

        default_events = await service.list_task_events(
            profile_id="default", task_id=default_task.id
        )
        ops_events = await service.list_task_events(profile_id="ops", task_id=ops_task.id)
        assert [event.event_type for event in default_events[:2]] == [
            "recovery_action_created",
            "lease_expired",
        ]
        assert all(item.event_type != "lease_expired" for item in ops_events)
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_sweep_can_be_scoped_to_owner_ref(tmp_path: Path) -> None:
    """Maintenance sweep should optionally repair stale claims only for one executor owner ref."""

    db_name = "taskflow_runtime_owner_ref_sweep.db"
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        taskflow_public_principal_required=False,
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="researcher",
        markdown="# Researcher\nFocus on stale research work.",
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="reviewer",
        markdown="# Reviewer\nFocus on stale review work.",
    )
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst",),
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
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
    service = TaskFlowService(factory, settings=settings)
    try:
        researcher_task = await service.create_task(
            profile_id="default",
            title="Researcher stale task",
            description="Repair only the researcher stale claim.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
        )
        reviewer_task = await service.create_task(
            profile_id="default",
            title="Reviewer stale task",
            description="Leave the reviewer stale claim untouched.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="reviewer",
        )
        stale_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            for task_id, claim_token, claimed_by in (
                (researcher_task.id, "stale-researcher-claim", "taskflow-runtime:researcher"),
                (reviewer_task.id, "stale-reviewer-claim", "taskflow-runtime:reviewer"),
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
            owner_ref="researcher",
            limit=10,
        )

        assert released_count == 1
        researcher_after = await service.get_task(profile_id="default", task_id=researcher_task.id)
        reviewer_after = await service.get_task(profile_id="default", task_id=reviewer_task.id)
        assert researcher_after.status == "todo"
        assert reviewer_after.status == "running"
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
            owner_type="employee",
            owner_ref="analyst",
        )
        second = await service.create_task(
            profile_id="default",
            title="Second stale duplicate",
            description="Also leave this duplicate stale until maintenance repairs it.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
        )

        async with engine.begin() as conn:
            await conn.execute(text("DROP INDEX ux_task_active_employee_owner"))
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
        assert "ux_task_active_employee_owner" in index_names
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
            settings=Settings(
                root_dir=tmp_path,
                db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_owner_ref_filter.db'}",
                taskflow_runtime_owner_ref="researcher",
                llm_max_iterations=10,
            ),
        )
        try:
            allowed = await service.create_task(
                profile_id="default",
                title="Allowed owner",
                description="Please handle researcher queue.",
                owner_type="employee",
                owner_ref="researcher",
                created_by_type="human",
                created_by_ref="cli",
            )
            skipped = await service.create_task(
                profile_id="default",
                title="Skipped owner",
                description="Please handle papercliper queue.",
                owner_type="employee",
                owner_ref="papercliper",
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
            settings=Settings(
                root_dir=tmp_path,
                db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_owner_ref_filter.db'}",
                llm_max_iterations=10,
            ),
        )
        try:
            processed = await unfiltered_runtime.execute_next_claimable_task(
                worker_id="taskflow-runtime:owner-unfiltered"
            )
            assert processed is True
            skipped_after_unfiltered = await service.get_task(
                profile_id="default", task_id=skipped.id
            )
            assert observed_calls[1].task_id == skipped.id
            assert skipped_after_unfiltered.status == "completed"
        finally:
            await unfiltered_runtime.shutdown()
    finally:
        await engine.dispose()


async def test_taskflow_runtime_respects_optional_profile_filter(tmp_path: Path) -> None:
    """Detached runtime profile filter should claim work only from the configured backlog profile."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="taskflow_runtime_profile_filter.db",
        profile_ids=("default", "ops", "analyst"),
    )
    service = TaskFlowService(factory)
    observed_calls: list[_ObservedCall] = []
    runtime = TaskFlowRuntimeService(
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
        settings=Settings(
            root_dir=tmp_path,
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_profile_filter.db'}",
            taskflow_runtime_profile_id="ops",
            llm_max_iterations=10,
        ),
    )
    try:
        skipped = await service.create_task(
            profile_id="default",
            title="Skipped backlog task",
            description="This task should stay untouched in the default backlog.",
            owner_type="employee",
            owner_ref="papercliper",
            created_by_type="human",
            created_by_ref="cli",
        )
        allowed = await service.create_task(
            profile_id="ops",
            title="Allowed ops task",
            description="This task should run because the runtime is pinned to the ops backlog.",
            owner_type="employee",
            owner_ref="papercliper",
            created_by_type="human",
            created_by_ref="cli",
        )

        processed = await runtime.execute_next_claimable_task(
            worker_id="taskflow-runtime:profile-filtered"
        )

        assert processed is True
        assert len(observed_calls) == 1
        assert observed_calls[0].task_id == allowed.id
        assert observed_calls[0].task_profile_id == "ops"
        skipped_after = await service.get_task(profile_id="default", task_id=skipped.id)
        allowed_after = await service.get_task(profile_id="ops", task_id=allowed.id)
        assert skipped_after.status == "todo"
        assert allowed_after.status == "completed"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_owner_ref_filter_matches_specific_employee(
    tmp_path: Path,
) -> None:
    """Detached runtime owner_ref filter should allow targeting one concrete employee executor."""

    db_name = "taskflow_runtime_owner_ref_subagent_filter.db"
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        taskflow_runtime_owner_ref="researcher",
        llm_max_iterations=10,
        taskflow_public_principal_required=False,
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="researcher",
        markdown="# Researcher\nFocus on research work.",
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="reviewer",
        markdown="# Reviewer\nFocus on review work.",
    )
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst",),
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory, settings=settings)
    observed_calls: list[_ObservedCall] = []
    runtime = TaskFlowRuntimeService(
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
        settings=settings,
    )
    try:
        allowed = await service.create_task(
            profile_id="default",
            title="Allowed researcher task",
            description="Execute only on the configured researcher subagent.",
            owner_type="employee",
            owner_ref="researcher",
            created_by_type="human",
            created_by_ref="cli",
        )
        skipped = await service.create_task(
            profile_id="default",
            title="Skipped reviewer task",
            description="This should wait because runtime is pinned to a different subagent.",
            owner_type="employee",
            owner_ref="reviewer",
            created_by_type="human",
            created_by_ref="cli",
        )

        processed = await runtime.execute_next_claimable_task(
            worker_id="taskflow-runtime:subagent-filtered"
        )

        assert processed is True
        assert len(observed_calls) == 1
        assert observed_calls[0].task_id == allowed.id
        assert observed_calls[0].profile_id == "default"
        allowed_after = await service.get_task(profile_id="default", task_id=allowed.id)
        skipped_after = await service.get_task(profile_id="default", task_id=skipped.id)
        assert allowed_after.status == "completed"
        assert skipped_after.status == "todo"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_executes_employee_task_with_subagent_overlay(
    tmp_path: Path,
) -> None:
    """Detached runtime should execute employee work on the host profile with subagent prompt."""

    db_name = "taskflow_runtime_employee_overlay.db"
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        llm_max_iterations=10,
        taskflow_public_principal_required=False,
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="researcher",
        markdown="# Researcher\nSubagent marker: deep-brief.",
    )
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst",),
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory, settings=settings)
    observed_calls: list[_ObservedCall] = []
    runtime = TaskFlowRuntimeService(
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="complete",
            observed_calls=observed_calls,
        ),
        settings=settings,
    )
    try:
        task = await service.create_task(
            profile_id="default",
            title="Subagent runtime task",
            description="Execute this as the analyst researcher subagent.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
        )

        processed = await runtime.execute_next_claimable_task(worker_id="taskflow-runtime:subagent")

        assert processed is True
        assert observed_calls[0].task_id == task.id
        assert observed_calls[0].profile_id == "default"
        assert observed_calls[0].prompt_overlay is not None
        assert "employee_id: researcher" in observed_calls[0].prompt_overlay
        assert "Task Flow execution context." in observed_calls[0].prompt_overlay
        assert "Task Flow worker protocol." in observed_calls[0].prompt_overlay
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_executes_review_task_with_employee_reviewer(
    tmp_path: Path,
) -> None:
    """Review tasks should be claimed by the assigned Employee reviewer, not by the task owner."""

    db_name = "taskflow_runtime_employee_review_claim.db"
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        llm_max_iterations=10,
        taskflow_public_principal_required=False,
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="reviewer",
        markdown="# Reviewer\nSubagent marker: review-queue.",
    )
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst",),
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory, settings=settings)
    observed_calls: list[_ObservedCall] = []
    runtime = TaskFlowRuntimeService(
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _FakeSessionRunner(
            session,
            behavior="approve_review",
            observed_calls=observed_calls,
        ),
        settings=settings,
    )
    try:
        task = await service.create_task(
            profile_id="default",
            title="Review employee-owned draft",
            description="Approve or request changes as the reviewer subagent.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="papercliper",
            reviewer_type="employee",
            reviewer_ref="reviewer",
        )
        await service.update_task(profile_id="default", task_id=task.id, status="review")

        processed = await runtime.execute_next_claimable_task(worker_id="taskflow-runtime:reviewer")

        assert processed is True
        assert observed_calls[0].task_id == task.id
        assert observed_calls[0].profile_id == "default"
        assert observed_calls[0].prompt_overlay is not None
        assert "employee_id: reviewer" in observed_calls[0].prompt_overlay
        assert "source_status: review" in observed_calls[0].prompt_overlay
        assert "executor: employee:reviewer" in observed_calls[0].prompt_overlay
        assert "Task Flow worker protocol." in observed_calls[0].prompt_overlay

        updated = await service.get_task(profile_id="default", task_id=task.id)
        assert updated.status == "completed"
        assert updated.owner_type == "employee"
        assert updated.owner_ref == "papercliper"

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            runs = await repo.list_task_runs(task_id=task.id, limit=1)
        assert runs[0].owner_type == "employee"
        assert runs[0].owner_ref == "reviewer"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_keeps_review_claims_scoped_to_reviewer(
    tmp_path: Path,
) -> None:
    """Detached scheduling should enforce reviewer concurrency for Employee review claims."""

    db_name = "taskflow_runtime_ai_review_claim_scope.db"
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        llm_max_iterations=10,
        taskflow_public_principal_required=False,
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="reviewer",
        markdown="# Reviewer\nFocus on reviews.",
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="auditor",
        markdown="# Auditor\nFocus on audits.",
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        first = await service.create_task(
            profile_id="default",
            title="First reviewer task",
            description="This reviewer should claim first.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="papercliper",
            reviewer_type="employee",
            reviewer_ref="reviewer",
            priority=90,
        )
        second_same_reviewer = await service.create_task(
            profile_id="default",
            title="Second reviewer task",
            description="This should wait for the same reviewer.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="papercliper",
            reviewer_type="employee",
            reviewer_ref="reviewer",
            priority=80,
        )
        auditor = await service.create_task(
            profile_id="default",
            title="Auditor review task",
            description="This can claim while reviewer is busy.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="papercliper",
            reviewer_type="employee",
            reviewer_ref="auditor",
            priority=70,
        )
        for item in (first, second_same_reviewer, auditor):
            await service.update_task(profile_id="default", task_id=item.id, status="review")

        claim_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            first_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-reviewer-1",
                claimed_by="taskflow-runtime:0",
            )
            second_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-auditor-1",
                claimed_by="taskflow-runtime:1",
            )
            third_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-review-none",
                claimed_by="taskflow-runtime:2",
            )

        assert first_claim is not None
        assert first_claim.id == first.id
        assert first_claim.claim_owner_type == "employee"
        assert first_claim.claim_owner_ref == "reviewer"
        assert first_claim.claim_source_status == "review"
        assert second_claim is not None
        assert second_claim.id == auditor.id
        assert third_claim is None

        waiting = await service.get_task(profile_id="default", task_id=second_same_reviewer.id)
        assert waiting.status == "review"
    finally:
        await engine.dispose()


async def test_taskflow_runtime_claims_only_one_active_task_per_distinct_employee(
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
        researcher_first = await service.create_task(
            profile_id="default",
            title="Researcher high priority",
            description="Take the highest-priority researcher task first.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
            priority=90,
        )
        researcher_second = await service.create_task(
            profile_id="default",
            title="Researcher second task",
            description="This should wait until researcher is free again.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
            priority=80,
        )
        papercliper_task = await service.create_task(
            profile_id="default",
            title="Papercliper task",
            description="Take this once the researcher already has active work.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
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
        assert first_claim.id == researcher_first.id
        assert second_claim is not None
        assert second_claim.id == papercliper_task.id
        assert third_claim is None

        waiting = await service.get_task(profile_id="default", task_id=researcher_second.id)
        assert waiting.status == "todo"
    finally:
        await engine.dispose()


async def test_taskflow_runtime_claims_only_one_active_task_per_employee(
    tmp_path: Path,
) -> None:
    """Detached scheduling should enforce one active task per employee executor."""

    db_name = "taskflow_runtime_per_subagent_limit.db"
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        llm_max_iterations=10,
        taskflow_public_principal_required=False,
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="researcher",
        markdown="# Researcher\nFocus on research tasks.",
    )
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="reviewer",
        markdown="# Reviewer\nFocus on review tasks.",
    )
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst",),
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        researcher_first = await service.create_task(
            profile_id="default",
            title="Researcher high priority",
            description="Take the highest-priority researcher task first.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
            priority=90,
        )
        researcher_second = await service.create_task(
            profile_id="default",
            title="Researcher second task",
            description="This should wait until the researcher subagent is free again.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
            priority=80,
        )
        reviewer_task = await service.create_task(
            profile_id="default",
            title="Reviewer task",
            description="This can run while the researcher subagent is busy.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="reviewer",
            priority=70,
        )

        claim_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            first_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-subagent-1",
                claimed_by="taskflow-runtime:0",
            )
            second_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-subagent-2",
                claimed_by="taskflow-runtime:1",
            )
            third_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-subagent-none",
                claimed_by="taskflow-runtime:2",
            )

        assert first_claim is not None
        assert first_claim.id == researcher_first.id
        assert second_claim is not None
        assert second_claim.id == reviewer_task.id
        assert third_claim is None

        waiting = await service.get_task(profile_id="default", task_id=researcher_second.id)
        assert waiting.status == "todo"
    finally:
        await engine.dispose()


async def test_taskflow_runtime_allows_same_ai_owner_ref_across_profiles(
    tmp_path: Path,
) -> None:
    """Claim guard should stay profile-scoped for shared Employee owner refs."""

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
            owner_type="employee",
            owner_ref="analyst",
            priority=90,
        )
        researcher_task = await service.create_task(
            profile_id="researcher",
            title="Researcher analyst claim",
            description="Claim analyst work in researcher profile.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
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
            owner_type="employee",
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
            owner_type="employee",
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
            owner_type="employee",
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
            owner_type="employee",
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
            owner_type="employee",
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
            owner_type="employee",
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
            owner_type="employee",
            owner_ref="analyst",
            priority=90,
        )
        no_flow_second = await service.create_task(
            profile_id="default",
            title="No-flow researcher",
            description="This should wait behind the idle flow bucket.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
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
            owner_type="employee",
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
                statement="UPDATE task SET status='claimed' /* ux_task_active_employee_owner */",
                params=None,
                orig=Exception(
                    "UNIQUE constraint failed: task.profile_id, task.owner_ref (ux_task_active_employee_owner)"
                ),
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
            owner_type="employee",
            owner_ref="papercliper",
            priority=90,
        )

        processed = await runtime.execute_next_claimable_task(worker_id="worker-retry-claim")

        assert processed is True
        assert claim_attempts >= 2
        task = (await service.list_tasks(profile_id="default"))[0]
        assert task.owner_ref == "papercliper"
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
            owner_type="employee",
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
    """Detached runtime should leave employee PLAN tasks untouched and run runnable work."""

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
        settings=Settings(
            root_dir=tmp_path,
            db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_runtime_plan_skip.db'}",
            taskflow_runtime_owner_ref="researcher",
        ),
    )
    try:
        planned = await service.create_task(
            profile_id="default",
            title="Draft the task before Employee starts",
            description="Stay in PLAN until the responsible employee finishes the brief.",
            status="plan",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
        )
        runnable = await service.create_task(
            profile_id="default",
            title="Run once planning is complete",
            description="This task is ready for the detached runtime.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
        )

        untouched = await service.create_task(
            profile_id="default",
            title="Other owner remains queued",
            description="Wait for another runtime worker",
            owner_type="employee",
            owner_ref="analyst",
            created_by_type="human",
            created_by_ref="cli",
        )

        processed = await runtime.execute_next_claimable_task(
            worker_id="taskflow-runtime:plan-skip"
        )

        assert processed is True
        planned_after = await service.get_task(profile_id="default", task_id=planned.id)
        runnable_after = await service.get_task(profile_id="default", task_id=runnable.id)
        untouched_after = await service.get_task(profile_id="default", task_id=untouched.id)
        assert planned_after.status == "plan"
        assert planned_after.blocked_reason_code is None
        assert planned_after.blocked_reason_text is None
        assert planned_after.last_error_code is None
        assert planned_after.last_error_text is None
        assert runnable_after.status == "completed"
        assert untouched_after.status == "todo"
        assert observed_calls[0].task_id == runnable.id

        events = await service.list_task_events(profile_id="default", task_id=planned.id)
        event_types = [event.event_type for event in events]
        assert "runtime_claim_rejected" not in event_types
        assert "blocked" not in event_types
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
            owner_type="employee",
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

        processed = await runtime.execute_next_claimable_task(
            worker_id="taskflow-runtime:attachments"
        )

        assert processed is True
        assert observed_calls[0].task_id == task.id
        assert "Task Attachments:" in observed_calls[0].message
        assert "brief.txt" in observed_calls[0].message
        assert "User needs: ship the description migration." in observed_calls[0].message
    finally:
        await runtime.shutdown()
        await engine.dispose()


def test_taskflow_context_overrides_include_runtime_task_guidance() -> None:
    """Task Flow prompt overlay should teach decomposition and employee handoff rules."""

    overrides = build_task_flow_context_overrides(
        task_id="task_demo",
        task_profile_id="default",
        owner_type="employee",
        owner_ref="analyst",
        executor_type="employee",
        executor_ref="analyst",
        source_status="todo",
        flow_id="flow_demo",
        source_type="manual",
        source_ref="source_demo",
        work_mode="execution",
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
    assert taskflow_payload["work_mode"] == "execution"
    assert overrides.execution_planning_mode == "on"
    assert overrides.prompt_overlay is not None
    assert "This runtime is non-interactive." in overrides.prompt_overlay
    assert "task.update" in overrides.prompt_overlay
    assert "task.block" in overrides.prompt_overlay
    assert "task.context.get" in overrides.prompt_overlay
    assert "task.doc.put" in overrides.prompt_overlay
    assert "task.doc.confirm" in overrides.prompt_overlay
    assert "task.feed.list" in overrides.prompt_overlay
    assert "task.flow.create" in overrides.prompt_overlay
    assert "task.comment.add" in overrides.prompt_overlay
    assert "task.delegate" in overrides.prompt_overlay
    assert "execution plan" in overrides.prompt_overlay
    assert "Employees are the only Task Flow owners" in overrides.prompt_overlay
    assert "task.list, task.board, task.stale.list, or task.stale.sweep" in overrides.prompt_overlay
    assert "owner_type=employee and owner_ref=<employee_id>" in overrides.prompt_overlay
    assert "task.review.list" in overrides.prompt_overlay
    assert "task.dependency.add" in overrides.prompt_overlay
    assert "retry_after_sec" in overrides.prompt_overlay
    assert "task.board" in overrides.prompt_overlay


def test_taskflow_work_mode_resolver_routes_manager_intake() -> None:
    """Manager and explicitly labeled intake tasks should route to orchestration mode."""

    assert (
        resolve_task_work_mode(
            source_type="gitlab_merge_request",
            labels=("mr-review",),
            executor_is_manager=False,
        )
        == "execution"
    )
    assert (
        resolve_task_work_mode(
            source_type="manual",
            labels=("manager-intake",),
            executor_is_manager=False,
        )
        == "manager_intake"
    )
    assert (
        resolve_task_work_mode(
            source_type="manual",
            labels=(),
            executor_is_manager=True,
        )
        == "manager_intake"
    )
    assert (
        resolve_task_work_mode(
            source_type="task_delegation",
            labels=("implementation",),
            executor_is_manager=False,
        )
        == "execution"
    )


def test_taskflow_context_overrides_include_worker_guidance_for_employees() -> None:
    """Focused employee Task Flow runs should receive worker-specific collaboration rules."""

    overrides = build_task_flow_context_overrides(
        task_id="task_worker",
        task_profile_id="default",
        owner_type="employee",
        owner_ref="default:backend-engineer",
        executor_type="employee",
        executor_ref="default:backend-engineer",
        source_status="todo",
        flow_id="flow_demo",
        source_type="manual",
        source_ref=None,
        work_mode="execution",
        priority=75,
        attempt=1,
        requires_review=False,
    )

    assert overrides.prompt_overlay is not None
    assert "Task Flow worker protocol." in overrides.prompt_overlay
    assert "task.context.get" in overrides.prompt_overlay
    assert "task.feed.list" in overrides.prompt_overlay
    assert "task.doc.put" in overrides.prompt_overlay
    assert "task.comment.add" in overrides.prompt_overlay
    assert "handoff" in overrides.prompt_overlay.lower()


def test_taskflow_context_overrides_include_orchestrator_guidance_for_managers() -> None:
    """Manager employee Task Flow runs should receive decomposition and review protocol."""

    overrides = build_task_flow_context_overrides(
        task_id="task_manager",
        task_profile_id="default",
        owner_type="employee",
        owner_ref="cto",
        executor_type="employee",
        executor_ref="cto",
        source_status="todo",
        flow_id="flow_demo",
        source_type="manual",
        source_ref=None,
        work_mode="manager_intake",
        priority=90,
        attempt=1,
        requires_review=True,
        executor_is_manager=True,
    )

    assert overrides.prompt_overlay is not None
    assert "Manager intake work mode." in overrides.prompt_overlay
    assert "Team Orchestrator protocol." in overrides.prompt_overlay
    assert "Task Flow worker protocol." not in overrides.prompt_overlay
    assert "Decompose large work" in overrides.prompt_overlay
    assert "Do not run implementation" in overrides.prompt_overlay


def test_taskflow_context_overrides_include_knowledge_maintenance_mode() -> None:
    """CTO maintenance tasks should be explicitly bounded to project knowledge work."""

    overrides = build_task_flow_context_overrides(
        task_id="task_knowledge",
        task_profile_id="default",
        owner_type="employee",
        owner_ref="cto",
        executor_type="employee",
        executor_ref="cto",
        source_status="todo",
        flow_id="flow_demo",
        source_type="knowledge_maintenance",
        source_ref="flow:flow_demo",
        work_mode="knowledge_maintenance",
        priority=85,
        attempt=1,
        requires_review=False,
        executor_is_manager=True,
    )

    assert isinstance(overrides.runtime_metadata, dict)
    taskflow_payload = overrides.runtime_metadata.get("taskflow")
    assert isinstance(taskflow_payload, dict)
    assert taskflow_payload["work_mode"] == "knowledge_maintenance"
    assert overrides.prompt_overlay is not None
    assert "Knowledge maintenance work mode." in overrides.prompt_overlay
    assert "Do not implement specialist work" in overrides.prompt_overlay
    assert "human_review_required" in overrides.prompt_overlay
