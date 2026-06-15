"""Task Flow employee ownership tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.services.agent_loop.action_contracts import ActionEnvelope, TurnResult
from afkbot.services.agent_loop.turn_context import TurnContextOverrides
from afkbot.services.task_flow import TaskFlowServiceError
from afkbot.services.task_flow.runtime_service import TaskFlowRuntimeService
from afkbot.services.task_flow.service import TaskFlowService
from afkbot.settings import Settings

_TRACKED_SERVICES: list[TaskFlowService] = []


@pytest.fixture(autouse=True)
async def _shutdown_tracked_services() -> AsyncIterator[None]:
    yield
    while _TRACKED_SERVICES:
        await _TRACKED_SERVICES.pop().shutdown()


async def _service(tmp_path: Path, db_name: str) -> TaskFlowService:
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        taskflow_public_principal_required=False,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
    service = TaskFlowService(factory, settings=settings, engine=engine)
    _TRACKED_SERVICES.append(service)
    return service


async def _runtime_fixture(tmp_path: Path, db_name: str):
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        taskflow_public_principal_required=False,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
    service = TaskFlowService(factory, settings=settings, engine=engine)
    _TRACKED_SERVICES.append(service)
    return settings, engine, factory, service


class _ObservedEmployeeRunner:
    def __init__(self, session: AsyncSession, observed: list[dict[str, object]]) -> None:
        self._session = session
        self._observed = observed

    async def run_turn(
        self,
        *,
        profile_id: str,
        session_id: str,
        message: str,
        context_overrides: object | None = None,
        source: str | None = None,
    ) -> TurnResult:
        metadata = (
            context_overrides.runtime_metadata
            if isinstance(context_overrides, TurnContextOverrides)
            else None
        )
        trusted = (
            context_overrides.trusted_runtime_context
            if isinstance(context_overrides, TurnContextOverrides)
            else None
        )
        self._observed.append(
            {
                "profile_id": profile_id,
                "session_id": session_id,
                "source": source,
                "metadata": metadata,
                "trusted": trusted,
                "prompt_overlay": (
                    context_overrides.prompt_overlay
                    if isinstance(context_overrides, TurnContextOverrides)
                    else None
                ),
            }
        )
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
            event_type="action",
            content=ActionEnvelope(action="finalize", message="done").model_dump_json(),
        )
        return TurnResult(done=True, final_text="done", run_id=run.id)


def _write_employee(
    settings_root: Path,
    *,
    profile_id: str,
    employee_id: str,
    manager_id: str | None = None,
    reports: tuple[str, ...] = (),
    status: str = "active",
) -> None:
    path = settings_root / "profiles" / profile_id / "employees" / f"{employee_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"id: {employee_id}",
        "name: CTO",
        "title: Technical Director",
        "role: cto",
        f"status: {status}",
    ]
    if manager_id:
        lines.append(f"manager_id: {manager_id}")
    if reports:
        lines.append("reports:")
        lines.extend(f"  - {item}" for item in reports)
    lines.extend(
        [
            "---",
            "# Responsibility",
            "Owns Task Flow decomposition.",
            "",
        ]
    )
    path.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )


async def test_task_flow_defaults_owner_to_single_root_employee(tmp_path: Path) -> None:
    service = await _service(tmp_path, "employee_default_owner.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto")

    task = await service.create_task(
        profile_id="default",
        title="Default employee owner",
        description="Owner should come from the org chart root.",
        created_by_type="human",
        created_by_ref="cli",
    )

    assert task.owner_type == "employee"
    assert task.owner_ref == "cto"


async def test_task_flow_requires_owner_when_root_employee_is_ambiguous(
    tmp_path: Path,
) -> None:
    service = await _service(tmp_path, "employee_default_owner_ambiguous.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    _write_employee(tmp_path, profile_id="default", employee_id="lead")

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.create_task(
            profile_id="default",
            title="Ambiguous owner",
            description="Multiple root employees require an explicit owner.",
            created_by_type="human",
            created_by_ref="cli",
        )

    assert exc_info.value.error_code == "task_employee_owner_required"


async def test_task_flow_accepts_existing_profile_employee_owner(tmp_path: Path) -> None:
    service = await _service(tmp_path, "employee_owner.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto")

    task = await service.create_task(
        profile_id="default",
        title="Implement employee flow",
        description="Build the employee-owned task flow path.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="cto",
    )

    assert task.owner_type == "employee"
    assert task.owner_ref == "cto"


async def test_root_employee_cannot_manage_unrelated_task_without_delegation(
    tmp_path: Path,
) -> None:
    service = await _service(tmp_path, "employee_root_scope_guard.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    _write_employee(tmp_path, profile_id="default", employee_id="developer")
    task = await service.create_task(
        profile_id="default",
        title="Unrelated employee task",
        description="A root employee is not a global dispatcher by default.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="developer",
    )

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.update_task(
            profile_id="default",
            task_id=task.id,
            owner_type="employee",
            owner_ref="cto",
            actor_type="employee",
            actor_ref="cto",
        )

    assert exc_info.value.error_code == "task_actor_forbidden"


async def test_root_employee_without_reports_cannot_manage_flows(tmp_path: Path) -> None:
    service = await _service(tmp_path, "employee_root_flow_scope_guard.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    flow = await service.create_flow(
        profile_id="default",
        title="Operator flow",
        description="Root employees without reports cannot mutate flow routing.",
        created_by_type="human",
        created_by_ref="cli",
    )

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.update_flow(
            profile_id="default",
            flow_id=flow.id,
            title="Taken over",
            actor_type="employee",
            actor_ref="cto",
        )

    assert exc_info.value.error_code == "task_actor_forbidden"


async def test_task_flow_rejects_missing_employee_owner(tmp_path: Path) -> None:
    service = await _service(tmp_path, "missing_employee_owner.db")

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.create_task(
            profile_id="default",
            title="Implement employee flow",
            description="Build the employee-owned task flow path.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="cto",
        )

    assert exc_info.value.error_code == "employee_not_found"


async def test_task_flow_rejects_disabled_employee_owner(tmp_path: Path) -> None:
    service = await _service(tmp_path, "disabled_employee_owner.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto", status="disabled")

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.create_task(
            profile_id="default",
            title="Disabled employee owner",
            description="Disabled employees cannot receive new work.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="cto",
        )

    assert exc_info.value.error_code == "invalid_owner_ref"


async def test_task_flow_rejects_legacy_ai_owner_types(tmp_path: Path) -> None:
    service = await _service(tmp_path, "legacy_owner_rejected.db")

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.create_task(
            profile_id="default",
            title="Legacy owner",
            description="Old profile executors are no longer Task Flow owners.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
        )

    assert exc_info.value.error_code == "invalid_owner_type"


async def test_task_comment_wakes_responsible_employee_feed(tmp_path: Path) -> None:
    service = await _service(tmp_path, "employee_comment_wake.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    task = await service.create_task(
        profile_id="default",
        title="Comment wake",
        description="Responsible employee should see comments in feed.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="cto",
    )

    await service.add_task_comment(
        profile_id="default",
        task_id=task.id,
        actor_type="human",
        actor_ref="cli",
        message="Please react to this update.",
    )

    feed = await service.build_employee_inbox(
        profile_id="default",
        owner_type="employee",
        owner_ref="cto",
    )
    assert feed.recent_events
    assert feed.recent_events[0].event_type == "wake_requested"
    assert feed.recent_events[0].details["reason_code"] == "comment_added"


async def test_manager_employee_can_comment_on_direct_report_task(tmp_path: Path) -> None:
    service = await _service(tmp_path, "employee_manager_comment.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto", reports=("developer",))
    _write_employee(tmp_path, profile_id="default", employee_id="developer", manager_id="cto")
    async with session_scope(service._session_factory) as session:  # type: ignore[attr-defined]
        await ChatSessionRepository(session).create(
            session_id="employee-manager-session",
            profile_id="default",
        )
    task = await service.create_task(
        profile_id="default",
        title="Managed work",
        description="A manager should be able to unblock direct report work.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="developer",
    )

    comment = await service.add_task_comment(
        profile_id="default",
        task_id=task.id,
        actor_type="employee",
        actor_ref="cto",
        actor_session_id="employee-manager-session",
        message="I reviewed the blocker.",
    )

    assert comment.message == "I reviewed the blocker."


async def test_manager_reassignment_blocker_wakes_employee_manager(tmp_path: Path) -> None:
    settings, engine, factory, service = await _runtime_fixture(
        tmp_path,
        "employee_manager_escalation.db",
    )
    _ = settings, engine
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="QA blocked backend remediation",
        description="A reviewer needs a manager to reassign implementation work.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )

    blocked = await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="manager_reassignment_required",
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    assert blocked.status == "blocked"
    assert blocked.owner_ref == "qa-reviewer"
    manager_tasks = await service.list_tasks(
        profile_id="default",
        owner_type="employee",
        owner_ref="qa",
    )
    escalation_task = next(
        item for item in manager_tasks if item.source_type == "manager_escalation"
    )
    assert escalation_task.status == "todo"
    assert escalation_task.source_ref == task.id
    assert "task_owner_forbidden" in escalation_task.description
    manager_feed = await service.build_employee_inbox(
        profile_id="default",
        owner_type="employee",
        owner_ref="qa",
    )
    assert any(item.id == escalation_task.id for item in manager_feed.tasks)
    assert manager_feed.recent_events
    assert any(
        event.event_type == "wake_requested"
        and event.details["escalation_type"] == "manager_reassignment"
        and event.details["source_owner_ref"] == "qa-reviewer"
        for event in manager_feed.recent_events
    )
    worker_feed = await service.build_employee_inbox(
        profile_id="default",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )
    assert all(item.id != task.id for item in worker_feed.tasks)
    async with session_scope(factory) as session:
        claim_now = datetime.now(timezone.utc)
        worker_claimed = await TaskFlowRepository(session).claim_next_runnable_task(
            now_utc=claim_now,
            lease_until=claim_now + timedelta(minutes=15),
            claim_token="claim-blocked-source",
            claimed_by="taskflow-runtime:qa-reviewer",
            profile_id="default",
            owner_ref="qa-reviewer",
        )
        claimed = await TaskFlowRepository(session).claim_next_runnable_task(
            now_utc=claim_now,
            lease_until=claim_now + timedelta(minutes=15),
            claim_token="claim-manager-escalation",
            claimed_by="taskflow-runtime:qa",
            profile_id="default",
            owner_ref="qa",
        )
    assert worker_claimed is None
    assert claimed is not None
    assert claimed.id == escalation_task.id
    assert claimed.claim_owner_ref == "qa"


async def test_manager_reassignment_blocker_rejects_timed_revisit(tmp_path: Path) -> None:
    service = await _service(tmp_path, "employee_manager_escalation_ready_at.db")
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="Timed manager handoff",
        description="Manager handoffs must park source work until routing changes.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.block_task(
            profile_id="default",
            task_id=task.id,
            reason_code="manager_reassignment_required",
            reason_text="Needs manager handoff after task_owner_forbidden.",
            actor_type="employee",
            actor_ref="qa-reviewer",
            ready_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    assert exc_info.value.error_code == "task_manager_escalation_ready_at_conflict"


async def test_orchestrator_handoff_blocker_parks_source_task(tmp_path: Path) -> None:
    settings, engine, factory, service = await _runtime_fixture(
        tmp_path,
        "employee_orchestrator_handoff_escalation.db",
    )
    _ = settings, engine
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="Orchestrator handoff blocker",
        description="A supported escalation blocker should park source work.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )

    blocked = await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="task_owner_forbidden",
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    assert blocked.ready_at is None
    assert blocked.blocked_reason_code == "manager_reassignment_required"
    worker_feed = await service.build_employee_inbox(
        profile_id="default",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )
    assert all(item.id != task.id for item in worker_feed.tasks)
    async with session_scope(factory) as session:
        claim_now = datetime.now(timezone.utc)
        worker_claimed = await TaskFlowRepository(session).claim_next_runnable_task(
            now_utc=claim_now,
            lease_until=claim_now + timedelta(minutes=15),
            claim_token="claim-orchestrator-handoff-source",
            claimed_by="taskflow-runtime:qa-reviewer",
            profile_id="default",
            owner_ref="qa-reviewer",
        )
    assert worker_claimed is None


async def test_review_changes_manager_handoff_creates_escalation_without_source_revisit(
    tmp_path: Path,
) -> None:
    settings, engine, factory, service = await _runtime_fixture(
        tmp_path,
        "employee_review_changes_manager_escalation.db",
    )
    _ = settings, engine
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="Review handoff blocker",
        description="Review changes can require manager routing.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa",
        reviewer_type="employee",
        reviewer_ref="qa-reviewer",
    )
    await service.update_task(profile_id="default", task_id=task.id, status="review")

    changed = await service.request_review_changes(
        profile_id="default",
        task_id=task.id,
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    assert changed.status == "blocked"
    assert changed.owner_ref == "qa-reviewer"
    assert changed.ready_at is None
    assert changed.blocked_reason_code == "manager_reassignment_required"
    async with session_scope(factory) as session:
        repo = TaskFlowRepository(session)
        escalations = await repo.list_tasks_by_source(
            profile_id="default",
            source_type="manager_escalation",
            source_ref=task.id,
        )
        claim_now = datetime.now(timezone.utc)
        worker_claimed = await repo.claim_next_runnable_task(
            now_utc=claim_now,
            lease_until=claim_now + timedelta(minutes=15),
            claim_token="claim-review-manager-source",
            claimed_by="taskflow-runtime:qa-reviewer",
            profile_id="default",
            owner_ref="qa-reviewer",
        )

    assert len(escalations) == 1
    assert escalations[0].owner_ref == "qa"
    assert worker_claimed is None


async def test_manager_handoff_update_clears_existing_ready_at(tmp_path: Path) -> None:
    settings, engine, factory, service = await _runtime_fixture(
        tmp_path,
        "employee_manager_escalation_ready_at_clear.db",
    )
    _ = settings, engine
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="Existing revisit blocker",
        description="Changing blocker class must clear stale ready_at.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )
    stale_ready_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="review_changes_requested",
        reason_text="Retry later.",
        actor_type="employee",
        actor_ref="qa-reviewer",
        ready_at=stale_ready_at,
    )

    updated = await service.update_task(
        profile_id="default",
        task_id=task.id,
        blocked_reason_code="task_owner_forbidden",
        blocked_reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    assert updated.blocked_reason_code == "manager_reassignment_required"
    assert updated.ready_at is None
    async with session_scope(factory) as session:
        claim_now = datetime.now(timezone.utc)
        claimed = await TaskFlowRepository(session).claim_next_runnable_task(
            now_utc=claim_now,
            lease_until=claim_now + timedelta(minutes=15),
            claim_token="claim-stale-ready-at-source",
            claimed_by="taskflow-runtime:qa-reviewer",
            profile_id="default",
            owner_ref="qa-reviewer",
        )
    assert claimed is None


async def test_manager_handoff_source_cannot_be_rearmed_without_blocker_fields(
    tmp_path: Path,
) -> None:
    service = await _service(tmp_path, "employee_manager_escalation_rearm_guard.db")
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="Parked handoff blocker",
        description="Omitting blocker fields must not clear manager handoff state.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )
    await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="manager_reassignment_required",
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="blocked",
            ready_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            actor_type="employee",
            actor_ref="qa-reviewer",
        )
    assert exc_info.value.error_code == "task_manager_escalation_ready_at_conflict"

    preserved = await service.update_task(
        profile_id="default",
        task_id=task.id,
        status="blocked",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )
    assert preserved.blocked_reason_code == "manager_reassignment_required"
    assert preserved.ready_at is None


async def test_manager_handoff_source_owner_cannot_move_source_back_to_todo(
    tmp_path: Path,
) -> None:
    service = await _service(tmp_path, "employee_manager_escalation_owner_rearm_guard.db")
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="Parked source cannot self-reactivate",
        description="Source owner must not resolve manager handoff blockers alone.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )
    await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="manager_reassignment_required",
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="todo",
            actor_type="employee",
            actor_ref="qa-reviewer",
        )

    assert exc_info.value.error_code == "task_manager_escalation_resolution_forbidden"
    refreshed = await service.get_task(profile_id="default", task_id=task.id)
    assert refreshed.status == "blocked"
    assert refreshed.blocked_reason_code == "manager_reassignment_required"
    assert refreshed.ready_at is None


async def test_direct_manager_can_resolve_manager_handoff_source(
    tmp_path: Path,
) -> None:
    service = await _service(tmp_path, "employee_manager_escalation_manager_resolves.db")
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="Manager resolves source blocker",
        description="Direct manager can deliberately requeue source work after triage.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )
    await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="manager_reassignment_required",
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    updated = await service.update_task(
        profile_id="default",
        task_id=task.id,
        status="todo",
        actor_type="employee",
        actor_ref="qa",
    )

    assert updated.status == "todo"
    assert updated.blocked_reason_code is None
    assert updated.blocked_reason_text is None
    assert updated.ready_at is not None


async def test_manager_reassignment_escalation_is_idempotent_for_repeated_wakes(
    tmp_path: Path,
) -> None:
    _, _, factory, service = await _runtime_fixture(
        tmp_path,
        "employee_manager_escalation_idempotent.db",
    )
    _write_employee(tmp_path, profile_id="default", employee_id="qa", reports=("qa-reviewer",))
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer", manager_id="qa")
    task = await service.create_task(
        profile_id="default",
        title="Repeated QA blocker",
        description="Repeated comments should not create duplicate manager tasks.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )

    await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="manager_reassignment_required",
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )
    await service.add_task_comment(
        profile_id="default",
        task_id=task.id,
        actor_type="human",
        actor_ref="cli",
        message="Проверь можешь ли ты начать задачу",
    )
    await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="manager_reassignment_required",
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    async with session_scope(factory) as session:
        escalations = await TaskFlowRepository(session).list_tasks_by_source(
            profile_id="default",
            source_type="manager_escalation",
            source_ref=task.id,
        )

    assert len(escalations) == 1
    assert escalations[0].owner_ref == "qa"
    assert escalations[0].status == "todo"


async def test_manager_reassignment_without_active_direct_manager_is_unroutable(
    tmp_path: Path,
) -> None:
    _, _, factory, service = await _runtime_fixture(
        tmp_path,
        "employee_manager_escalation_unroutable.db",
    )
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    _write_employee(tmp_path, profile_id="default", employee_id="developer")
    _write_employee(tmp_path, profile_id="default", employee_id="qa-reviewer")
    task = await service.create_task(
        profile_id="default",
        title="Missing manager blocker",
        description="Unrouted blockers must not leak to an unrelated root employee.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="qa-reviewer",
    )

    await service.block_task(
        profile_id="default",
        task_id=task.id,
        reason_code="manager_reassignment_required",
        reason_text="Needs manager handoff after task_owner_forbidden.",
        actor_type="employee",
        actor_ref="qa-reviewer",
    )

    async with session_scope(factory) as session:
        repo = TaskFlowRepository(session)
        escalations = await repo.list_tasks_by_source(
            profile_id="default",
            source_type="manager_escalation",
            source_ref=task.id,
        )
        events = await repo.list_task_events(task_id=task.id, limit=20)

    assert escalations == []
    assert any(event.event_type == "manager_escalation_unroutable" for event in events)

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.update_task(
            profile_id="default",
            task_id=task.id,
            owner_type="employee",
            owner_ref="developer",
            actor_type="employee",
            actor_ref="cto",
        )
    assert exc_info.value.error_code == "task_actor_forbidden"


async def test_task_comment_ignores_invalid_or_inactive_employee_mentions(
    tmp_path: Path,
) -> None:
    service = await _service(tmp_path, "employee_invalid_mentions.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    _write_employee(tmp_path, profile_id="default", employee_id="qa", status="disabled")
    task = await service.create_task(
        profile_id="default",
        title="Employee-owned work",
        description="Mentions should only wake active employees in this profile.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="employee",
        owner_ref="cto",
    )

    await service.add_task_comment(
        profile_id="default",
        task_id=task.id,
        actor_type="human",
        actor_ref="cli",
        message="@ghost @qa please check this.",
    )

    feed = await service.build_employee_inbox(
        profile_id="default",
        owner_type="employee",
        owner_ref="cto",
    )
    assert feed.mention_event_count == 0
    assert all(event.event_type != "mention_created" for event in feed.recent_events)


async def test_taskflow_runtime_executes_employee_with_employee_overlay(
    tmp_path: Path,
) -> None:
    settings, engine, factory, service = await _runtime_fixture(
        tmp_path,
        "employee_runtime.db",
    )
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    observed: list[dict[str, object]] = []
    runtime = TaskFlowRuntimeService(
        settings=settings,
        session_factory=factory,
        session_runner_factory=lambda session, _profile_id: _ObservedEmployeeRunner(
            session,
            observed,
        ),
    )
    try:
        task = await service.create_task(
            profile_id="default",
            title="Runtime employee task",
            description="Execute through employee runtime.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="cto",
        )

        assert await runtime.execute_next_claimable_task(worker_id="worker-employee") is True

        assert len(observed) == 1
        call = observed[0]
        assert call["profile_id"] == "default"
        assert call["session_id"] == f"taskflow:{task.id}"
        assert call["source"] == "taskflow"
        metadata = call["metadata"]
        assert isinstance(metadata, dict)
        taskflow = metadata["taskflow"]
        assert isinstance(taskflow, dict)
        assert taskflow["executor_type"] == "employee"
        assert taskflow["executor_ref"] == "cto"
        trusted = call["trusted"]
        assert isinstance(trusted, dict)
        detached = trusted["taskflow_detached_runtime"]
        assert isinstance(detached, dict)
        assert detached["owner_type"] == "employee"
        assert detached["owner_ref"] == "cto"
        prompt_overlay = call["prompt_overlay"]
        assert isinstance(prompt_overlay, str)
        assert "Task Flow execution context." in prompt_overlay
        assert "Employee execution context." in prompt_overlay
        assert "employee_id: cto" in prompt_overlay
        assert "Technical Director" in prompt_overlay
        assert "Team Orchestrator protocol." in prompt_overlay
        assert "ai_subagent" not in prompt_overlay
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_taskflow_runtime_blocks_claim_when_employee_becomes_disabled(
    tmp_path: Path,
) -> None:
    settings, engine, factory, service = await _runtime_fixture(
        tmp_path,
        "employee_runtime_disabled.db",
    )
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    runtime = TaskFlowRuntimeService(settings=settings, session_factory=factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Runtime disabled employee task",
            description="This should be blocked before employee execution.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="cto",
        )
        _write_employee(tmp_path, profile_id="default", employee_id="cto", status="disabled")

        assert await runtime.execute_next_claimable_task(worker_id="worker-disabled") is False

        blocked = await service.get_task(profile_id="default", task_id=task.id)
        assert blocked.status == "blocked"
        assert blocked.blocked_reason_code == "task_employee_inactive"
        assert blocked.blocked_reason_text is not None
        assert "disabled" in blocked.blocked_reason_text
    finally:
        await runtime.shutdown()
        await engine.dispose()
