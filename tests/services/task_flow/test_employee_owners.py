"""Task Flow employee ownership tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
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

    feed = await service.build_agent_inbox(
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
    service = await _service(tmp_path, "employee_manager_escalation.db")
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
    manager_feed = await service.build_agent_inbox(
        profile_id="default",
        owner_type="employee",
        owner_ref="qa",
    )
    assert manager_feed.recent_events
    assert manager_feed.recent_events[0].event_type == "wake_requested"
    assert manager_feed.recent_events[0].details["escalation_type"] == "manager_reassignment"
    assert manager_feed.recent_events[0].details["source_owner_ref"] == "qa-reviewer"


async def test_task_comment_ignores_invalid_or_inactive_employee_mentions(
    tmp_path: Path,
) -> None:
    service = await _service(tmp_path, "employee_invalid_mentions.db")
    _write_employee(tmp_path, profile_id="default", employee_id="cto")
    _write_employee(tmp_path, profile_id="default", employee_id="qa", status="disabled")
    task = await service.create_task(
        profile_id="default",
        title="Human-owned work",
        description="Mentions should only wake active employees in this profile.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="human",
        owner_ref="cli",
    )

    await service.add_task_comment(
        profile_id="default",
        task_id=task.id,
        actor_type="human",
        actor_ref="cli",
        message="@ghost @qa please check this.",
    )

    feed = await service.build_agent_inbox(
        profile_id="default",
        owner_type="employee",
        owner_ref="cto",
    )
    assert feed.recent_events == ()


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
