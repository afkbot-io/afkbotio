"""Tests for the Task Flow service layer."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory
from afkbot.db.session import session_scope
from afkbot.models.task_document import TaskDocument
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.chat_session_turn_queue_repo import ChatSessionTurnQueueRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.task_flow import TaskFlowServiceError
from afkbot.services.task_flow.knowledge_spine import (
    CANONICAL_FLOW_DOCUMENT_KEYS,
    build_knowledge_packet,
)
from afkbot.services.task_flow.service import TaskFlowService, _MAX_TASK_ATTACHMENT_BASE64_BYTES
from afkbot.settings import Settings
from tests.repositories._harness import build_repository_factory, _write_test_employees


def _taskflow_test_settings(
    *,
    tmp_path: Path,
    db_name: str,
    taskflow_public_principal_required: bool = False,
) -> Settings:
    return Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        chat_human_owner_ref="cli",
        taskflow_public_principal_required=taskflow_public_principal_required,
    )


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


def _write_legacy_team_runtime_config(
    *,
    settings: Settings,
    profile_id: str,
    team_profile_ids: tuple[str, ...],
) -> None:
    _ = (settings, profile_id, team_profile_ids)


async def _create_chat_session(
    factory,
    *,
    profile_id: str,
    session_id: str,
    active: bool = True,
) -> None:
    async with session_scope(factory) as session:
        sessions = ChatSessionRepository(session)
        if await sessions.get(session_id) is None:
            await sessions.create(session_id=session_id, profile_id=profile_id)
        if active:
            await ChatSessionTurnQueueRepository(session).enqueue(
                profile_id=profile_id,
                session_id=session_id,
                owner_token=f"pytest:{profile_id}:{session_id}",
                source="pytest",
                client_msg_id=None,
            )


async def _create_automation_actor(
    factory,
    *,
    profile_id: str,
    name: str,
) -> str:
    async with session_scope(factory) as session:
        repo = AutomationRepository(session)
        automation, _ = await repo.create_cron_automation(
            profile_id=profile_id,
            name=name,
            prompt="automation actor seed",
            cron_expr="0 * * * *",
            timezone="UTC",
            next_run_at=None,
        )
        return f"automation:{profile_id}:{automation.id}"


async def test_create_task_serializes_sqlite_read_before_write_under_pool_pressure(
    tmp_path: Path,
) -> None:
    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'taskflow_pool_pressure.db'}",
        root_dir=tmp_path,
        db_pool_size=1,
        db_max_overflow=0,
        db_pool_timeout_sec=1,
        taskflow_public_principal_required=False,
    )
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    _write_test_employees(settings=settings, profile_id="default")

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")

        service = TaskFlowService(factory, settings=settings, engine=engine)

        async def _create(index: int) -> str:
            task = await service.create_task(
                profile_id="default",
                title=f"Pool pressure task {index}",
                description="Exercise concurrent read-before-write task creation.",
                created_by_type="human",
                created_by_ref="pytest",
                owner_type="employee",
                owner_ref="default",
            )
            return task.id

        task_ids = await asyncio.gather(*(_create(index) for index in range(20)))

        assert len(set(task_ids)) == 20
    finally:
        await engine.dispose()


async def test_task_flow_service_uses_flow_owner_defaults_and_dependencies(
    tmp_path: Path,
) -> None:
    """Tasks should inherit flow owners and become blocked when dependencies are attached."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_service.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Launch prep",
            description="Prepare the launch backlog",
            created_by_type="human",
            created_by_ref="cli",
            default_owner_type="employee",
            default_owner_ref="default",
            labels=("launch",),
        )
        assert flow.default_owner_type == "employee"
        assert flow.default_owner_ref == "default"

        first_task = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Draft release notes",
            description="Prepare release notes from the current changelog.",
            created_by_type="human",
            created_by_ref="cli",
        )
        assert first_task.flow_id == flow.id
        assert first_task.owner_type == "employee"
        assert first_task.owner_ref == "default"
        assert first_task.status == "todo"

        dependent_task = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Publish release post",
            description="Post the release announcement after notes are ready.",
            created_by_type="human",
            created_by_ref="cli",
            depends_on_task_ids=(first_task.id,),
        )
        assert dependent_task.owner_ref == "default"
        assert dependent_task.status == "blocked"
        assert dependent_task.blocked_reason_code == "dependency_wait"
        assert dependent_task.depends_on_task_ids == (first_task.id,)

        await service.update_task(
            profile_id="default",
            task_id=first_task.id,
            status="completed",
        )
        unblocked_task = await service.get_task(profile_id="default", task_id=dependent_task.id)
        assert unblocked_task.status == "todo"
        assert unblocked_task.blocked_reason_code is None
        assert unblocked_task.depends_on_task_ids == (first_task.id,)

        listed = await service.list_tasks(
            profile_id="default",
            owner_type="employee",
            owner_ref="default",
        )
        assert {item.id for item in listed} == {first_task.id, dependent_task.id}
        listed_by_id = {item.id: item for item in listed}
        assert listed_by_id[dependent_task.id].depends_on_task_ids == (first_task.id,)
    finally:
        await engine.dispose()


async def test_task_flow_service_updates_flow_metadata_without_changing_identity(
    tmp_path: Path,
) -> None:
    """Flow edits should preserve ids and linked tasks while updating project metadata."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_update.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Old project name",
            description="Old project scope",
            created_by_type="human",
            created_by_ref="cli",
            default_owner_type="employee",
            default_owner_ref="default",
            labels=("old",),
        )
        task = await service.create_task(
            profile_id="default",
            title="Linked task",
            description="Keep this task attached to the edited flow.",
            created_by_type="human",
            created_by_ref="cli",
            flow_id=flow.id,
        )

        updated = await service.update_flow(
            profile_id="default",
            flow_id=flow.id,
            title="Renamed project",
            description="Updated project scope",
            default_owner_type="employee",
            default_owner_ref="default",
            labels=("new", "delivery"),
        )

        assert updated.id == flow.id
        assert updated.title == "Renamed project"
        assert updated.description == "Updated project scope"
        assert updated.default_owner_type == "employee"
        assert updated.default_owner_ref == "default"
        assert updated.labels == ("new", "delivery")

        detail = await service.get_task(profile_id="default", task_id=task.id)
        assert detail.flow_id == flow.id

        with pytest.raises(TaskFlowServiceError) as excinfo:
            await service.update_flow(
                profile_id="default",
                flow_id="missing-flow",
                title="Nope",
            )
        assert excinfo.value.error_code == "task_flow_not_found"
    finally:
        await engine.dispose()


async def test_task_flow_service_creates_default_flow_documents_and_revisions(
    tmp_path: Path,
) -> None:
    """Flows should carry canonical project docs for employee-owned execution."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_documents.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Agent operating model",
            description="Coordinate an employee-led Task Flow project.",
            created_by_type="human",
            created_by_ref="cli",
        )

        documents = await service.list_flow_documents(
            profile_id="default",
            flow_id=flow.id,
        )
        by_key = {document.document_key: document for document in documents}
        assert {"brief", "plan", "spec", "decisions", "status"}.issubset(by_key)
        assert "roadmap" not in by_key
        assert by_key["brief"].revision == 1
        assert by_key["brief"].scope_type == "flow"
        assert by_key["brief"].scope_id == flow.id
        assert "Agent operating model" in by_key["brief"].body
        assert "Project Status" in by_key["status"].body

        updated = await service.put_flow_document(
            profile_id="default",
            flow_id=flow.id,
            document_key="plan",
            title="Execution plan",
            body="1. Capture context.\n2. Decompose work.\n3. Verify output.",
            actor_type="human",
            actor_ref="cli",
            base_revision=by_key["plan"].revision,
        )
        assert updated.document_key == "plan"
        assert updated.revision == by_key["plan"].revision + 1
        assert updated.body.startswith("1. Capture context.")

        revisions = await service.list_document_revisions(
            profile_id="default",
            document_id=updated.id,
        )
        assert [revision.revision for revision in revisions] == [2, 1]
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_non_canonical_document_keys(
    tmp_path: Path,
) -> None:
    """New Task Flow docs should stay inside the canonical Knowledge Spine keys."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_document_key_contract.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Canonical docs",
            description="Keep project memory constrained.",
            created_by_type="human",
            created_by_ref="cli",
        )
        task = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Canonical task docs",
            description="Task documents should be working artifacts only.",
            created_by_type="human",
            created_by_ref="cli",
        )

        with pytest.raises(TaskFlowServiceError) as flow_exc:
            await service.put_flow_document(
                profile_id="default",
                flow_id=flow.id,
                document_key="roadmap",
                title="Legacy roadmap",
                body="This key should not be accepted in the new model.",
                actor_type="human",
                actor_ref="cli",
            )
        assert flow_exc.value.error_code == "invalid_task_document_key"

        with pytest.raises(TaskFlowServiceError) as task_exc:
            await service.put_task_document(
                profile_id="default",
                task_id=task.id,
                document_key="spec",
                title="Task spec",
                body="Specs belong to the flow-level spine.",
                actor_type="human",
                actor_ref="cli",
            )
        assert task_exc.value.error_code == "invalid_task_document_key"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_legacy_document_ids_on_current_surfaces(
    tmp_path: Path,
) -> None:
    """Legacy document rows must not remain active through direct id APIs."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_legacy_document_id_rejected.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Legacy direct ids",
            description="Keep old rows out of current document surfaces.",
            created_by_type="human",
            created_by_ref="cli",
        )
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            legacy = await repo.create_task_document(
                document_id="doc-legacy-roadmap-direct",
                profile_id="default",
                scope_type="flow",
                scope_id=flow.id,
                document_key="roadmap",
                title="Legacy roadmap",
                body="This old key should not be exposed by id.",
                created_by_type="human",
                created_by_ref="cli",
            )

        for operation in (
            lambda: service.get_document(profile_id="default", document_id=legacy.id),
            lambda: service.list_document_revisions(profile_id="default", document_id=legacy.id),
            lambda: service.confirm_document(
                profile_id="default",
                document_id=legacy.id,
                actor_type="human",
                actor_ref="cli",
            ),
            lambda: service.delete_document(
                profile_id="default",
                document_id=legacy.id,
                actor_type="human",
                actor_ref="cli",
            ),
        ):
            with pytest.raises(TaskFlowServiceError) as excinfo:
                await operation()
            assert excinfo.value.error_code == "invalid_task_document_key"
    finally:
        await engine.dispose()


async def test_task_flow_service_confirms_documents_with_public_local_human_principal(
    tmp_path: Path,
) -> None:
    """Public document confirmation should accept the validated local human principal."""

    db_name = "task_flow_public_document_local_human.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Public document confirmation",
            description="Confirm docs from an authenticated UI route.",
            created_by_type="human",
            created_by_ref="cli",
        )
        document = next(
            item
            for item in await service.list_flow_documents(profile_id="default", flow_id=flow.id)
            if item.document_key == "plan"
        )

        confirmed = await service.confirm_document(
            profile_id="default",
            document_id=document.id,
            actor_type="human",
            actor_ref="cli",
            actor_session_id="ui-session",
            expected_revision=document.revision,
        )
        deleted = await service.delete_document(
            profile_id="default",
            document_id=confirmed.id,
            actor_type="human",
            actor_ref="cli",
            actor_session_id="ui-session",
            expected_revision=confirmed.revision,
        )

        assert confirmed.confirmation_status == "confirmed"
        assert deleted.id == document.id
    finally:
        await engine.dispose()


async def test_knowledge_maintenance_creates_root_task_for_unconfirmed_flow_docs(
    tmp_path: Path,
) -> None:
    """Unconfirmed canonical docs should create one root employee maintenance task."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="knowledge_maintenance_create.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Knowledge project",
            description="Keep docs current.",
            created_by_type="human",
            created_by_ref="cli",
        )

        sweep = await service.ensure_knowledge_maintenance_tasks(profile_id="default")

        assert sweep.checked_flow_count == 1
        assert sweep.created_task_count == 1
        assert sweep.woken_task_count == 0
        item = sweep.flows[0]
        assert item.action == "created"
        assert item.flow_id == flow.id
        assert item.unconfirmed_flow_document_keys == CANONICAL_FLOW_DOCUMENT_KEYS
        assert item.task is not None
        assert item.task.owner_type == "employee"
        assert item.task.owner_ref == "default"
        assert item.task.source_type == "knowledge_maintenance"
        assert item.task.source_ref == f"flow:{flow.id}"
        assert "knowledge-maintenance" in item.task.labels
        assert "human_review_required" in item.task.description
    finally:
        await engine.dispose()


async def test_knowledge_maintenance_wakes_existing_task_without_duplicates(
    tmp_path: Path,
) -> None:
    """Repeated maintenance sweeps should wake, not duplicate, open CTO work."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="knowledge_maintenance_idempotent.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Repeated maintenance",
            description="Repeated sweeps should not duplicate work.",
            created_by_type="human",
            created_by_ref="cli",
        )

        first = await service.ensure_knowledge_maintenance_tasks(profile_id="default")
        second = await service.ensure_knowledge_maintenance_tasks(profile_id="default")

        assert first.created_task_count == 1
        assert second.created_task_count == 0
        assert second.woken_task_count == 1
        async with session_scope(factory) as session:
            tasks = await TaskFlowRepository(session).list_tasks_by_source(
                profile_id="default",
                source_type="knowledge_maintenance",
                source_ref=f"flow:{flow.id}",
            )
        assert len(tasks) == 1
    finally:
        await engine.dispose()


async def test_knowledge_maintenance_skips_confirmed_healthy_flow(tmp_path: Path) -> None:
    """Confirmed canonical docs should not create autonomous maintenance work."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="knowledge_maintenance_healthy.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Healthy knowledge",
            description="All docs confirmed.",
            created_by_type="human",
            created_by_ref="cli",
        )
        docs = await service.list_flow_documents(profile_id="default", flow_id=flow.id)
        for document in docs:
            await service.confirm_document(
                profile_id="default",
                document_id=document.id,
                actor_type="human",
                actor_ref="cli",
                expected_revision=document.revision,
            )

        sweep = await service.ensure_knowledge_maintenance_tasks(profile_id="default")

        assert sweep.created_task_count == 0
        assert sweep.woken_task_count == 0
        assert sweep.skipped_flow_count == 1
        assert sweep.flows[0].action == "healthy"
        assert sweep.flows[0].health_status == "ready"
    finally:
        await engine.dispose()


def test_knowledge_packet_excludes_legacy_document_keys() -> None:
    """Legacy rows should not shape runtime context after the Spine contract."""

    packet = build_knowledge_packet(
        profile_id="default",
        flow_id="flow-1",
        task_id="task-1",
        flow_documents=(
            TaskDocument(
                id="doc-brief",
                profile_id="default",
                scope_type="flow",
                scope_id="flow-1",
                document_key="brief",
                title="Brief",
                body="Project brief body",
                created_by_type="human",
                created_by_ref="cli",
                updated_by_type="human",
                updated_by_ref="cli",
            ),
            TaskDocument(
                id="doc-roadmap",
                profile_id="default",
                scope_type="flow",
                scope_id="flow-1",
                document_key="roadmap",
                title="Legacy roadmap",
                body="This legacy content must not enter the runtime packet.",
                created_by_type="human",
                created_by_ref="cli",
                updated_by_type="human",
                updated_by_ref="cli",
            ),
        ),
        task_documents=(
            TaskDocument(
                id="doc-handoff",
                profile_id="default",
                scope_type="task",
                scope_id="task-1",
                document_key="handoff",
                title="Handoff",
                body="Task handoff body",
                created_by_type="employee",
                created_by_ref="default",
                updated_by_type="employee",
                updated_by_ref="default",
            ),
            TaskDocument(
                id="doc-task-spec",
                profile_id="default",
                scope_type="task",
                scope_id="task-1",
                document_key="spec",
                title="Legacy task spec",
                body="This old task spec must not enter the runtime packet.",
                created_by_type="employee",
                created_by_ref="default",
                updated_by_type="employee",
                updated_by_ref="default",
            ),
        ),
    )

    assert [document.document_key for document in packet.documents] == ["brief", "handoff"]
    assert "roadmap" not in {document.document_key for document in packet.documents}
    assert "spec" not in {document.document_key for document in packet.documents}
    assert packet.unconfirmed_flow_document_keys == ("brief",)
    assert packet.health_status == "needs_attention"
    assert packet.ready_for_delegation is False
    assert packet.ready_for_execution is False
    assert packet.blocking_reasons == (
        "missing_planning_docs:plan,spec",
        "unconfirmed_planning_docs:brief",
    )
    assert packet.required_flow_document_keys == ("brief", "plan", "spec")


def test_knowledge_packet_reports_unconfirmed_canonical_flow_documents() -> None:
    """Runtime should know when project knowledge exists but still needs approval."""

    packet = build_knowledge_packet(
        profile_id="default",
        flow_id="flow-1",
        task_id="task-1",
        flow_documents=(
            TaskDocument(
                id="doc-brief",
                profile_id="default",
                scope_type="flow",
                scope_id="flow-1",
                document_key="brief",
                title="Brief",
                body="Confirmed brief.",
                revision=2,
                confirmation_status="confirmed",
                confirmed_revision=2,
                created_by_type="human",
                created_by_ref="cli",
                updated_by_type="human",
                updated_by_ref="cli",
            ),
            TaskDocument(
                id="doc-plan",
                profile_id="default",
                scope_type="flow",
                scope_id="flow-1",
                document_key="plan",
                title="Plan",
                body="Draft plan.",
                revision=3,
                confirmation_status="draft",
                confirmed_revision=None,
                created_by_type="employee",
                created_by_ref="cto",
                updated_by_type="employee",
                updated_by_ref="cto",
            ),
            TaskDocument(
                id="doc-spec",
                profile_id="default",
                scope_type="flow",
                scope_id="flow-1",
                document_key="spec",
                title="Spec",
                body="Stale confirmed revision.",
                revision=4,
                confirmation_status="confirmed",
                confirmed_revision=3,
                created_by_type="employee",
                created_by_ref="cto",
                updated_by_type="employee",
                updated_by_ref="cto",
            ),
        ),
        task_documents=(),
    )

    assert packet.unconfirmed_flow_document_keys == ("plan", "spec")
    assert packet.health_status == "needs_attention"
    assert packet.ready_for_delegation is False
    assert packet.blocking_reasons == ("unconfirmed_planning_docs:plan,spec",)


async def test_task_flow_service_lists_documents_across_scopes_with_filters(
    tmp_path: Path,
) -> None:
    """Document workspace queries should cover flow and task docs without leaking profiles."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_document_workspace.db",
        profile_ids=("default", "other"),
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Release coordination",
            description="Coordinate release documentation.",
            created_by_type="human",
            created_by_ref="cli",
        )
        other_flow = await service.create_flow(
            profile_id="other",
            title="Other profile",
            description="Must not leak into default profile queries.",
            created_by_type="human",
            created_by_ref="cli",
        )
        second_flow = await service.create_flow(
            profile_id="default",
            title="Mobile release",
            description="A second project should keep its own spine docs.",
            created_by_type="human",
            created_by_ref="cli",
        )
        task = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Draft launch notes",
            description="Prepare release handoff details.",
            created_by_type="human",
            created_by_ref="cli",
        )
        await service.put_task_document(
            profile_id="default",
            task_id=task.id,
            document_key="handoff",
            title="Launch handoff",
            body="Release blockers, owner map, and rollout notes.",
            actor_type="employee",
            actor_ref="default",
        )
        await service.put_flow_document(
            profile_id="other",
            flow_id=other_flow.id,
            document_key="plan",
            title="Hidden plan",
            body="Hidden release content.",
            actor_type="human",
            actor_ref="cli",
        )

        found = await service.list_documents(
            profile_id="default",
            query="release",
            scope_type="task",
            confirmation_status="draft",
        )

        assert [document.document_key for document in found] == ["handoff"]
        assert found[0].scope_id == task.id
        assert found[0].title == "Launch handoff"

        flow_briefs = await service.list_documents(
            profile_id="default",
            scope_type="flow",
            document_key="brief",
        )
        assert {document.scope_id for document in flow_briefs} == {flow.id, second_flow.id}

        limited = await service.list_documents(profile_id="default", limit=1)
        assert len(limited) == 1

        detail = await service.get_document(profile_id="default", document_id=found[0].id)
        assert detail.id == found[0].id
        assert "rollout notes" in detail.body

        with pytest.raises(TaskFlowServiceError) as excinfo:
            await service.get_document(profile_id="other", document_id=found[0].id)
        assert excinfo.value.error_code == "task_document_not_found"

        deleted = await service.delete_document(
            profile_id="default",
            document_id=found[0].id,
            actor_type="human",
            actor_ref="cli",
            expected_revision=found[0].revision,
        )
        assert deleted.id == found[0].id
        assert await service.list_documents(
            profile_id="default",
            query="release",
            scope_type="task",
        ) == []
        with pytest.raises(TaskFlowServiceError) as deleted_excinfo:
            await service.list_document_revisions(
                profile_id="default",
                document_id=found[0].id,
            )
        assert deleted_excinfo.value.error_code == "task_document_not_found"
    finally:
        await engine.dispose()


async def test_task_flow_service_scopes_document_mutations_to_owner_or_manager(
    tmp_path: Path,
) -> None:
    """Document writes should follow the same employee boundary as task mutations."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_document_actor_scope.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Scoped documentation",
            description="Keep project memory within the responsible employee chain.",
            created_by_type="human",
            created_by_ref="cli",
        )
        task = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Analyst-owned package",
            description="Only the owner, a manager, or a human can mutate docs.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
        )

        owner_doc = await service.put_task_document(
            profile_id="default",
            task_id=task.id,
            document_key="handoff",
            title="Analyst handoff",
            body="The analyst owns this task-level handoff.",
            actor_type="employee",
            actor_ref="analyst",
        )

        with pytest.raises(TaskFlowServiceError) as task_write_exc:
            await service.put_task_document(
                profile_id="default",
                task_id=task.id,
                document_key="notes",
                title="Unscoped notes",
                body="A sibling employee must not write here.",
                actor_type="employee",
                actor_ref="auditor",
            )
        assert task_write_exc.value.error_code == "task_actor_forbidden"

        with pytest.raises(TaskFlowServiceError) as confirm_exc:
            await service.confirm_document(
                profile_id="default",
                document_id=owner_doc.id,
                actor_type="employee",
                actor_ref="auditor",
                expected_revision=owner_doc.revision,
            )
        assert confirm_exc.value.error_code == "task_actor_forbidden"

        confirmed = await service.confirm_document(
            profile_id="default",
            document_id=owner_doc.id,
            actor_type="employee",
            actor_ref="default",
            expected_revision=owner_doc.revision,
        )
        assert confirmed.confirmation_status == "confirmed"
        assert confirmed.confirmed_by_ref == "default"

        with pytest.raises(TaskFlowServiceError) as delete_exc:
            await service.delete_document(
                profile_id="default",
                document_id=owner_doc.id,
                actor_type="employee",
                actor_ref="auditor",
                expected_revision=owner_doc.revision,
            )
        assert delete_exc.value.error_code == "task_actor_forbidden"

        manager_doc = await service.put_task_document(
            profile_id="default",
            task_id=task.id,
            document_key="evidence",
            title="Manager evidence",
            body="A manager can add task evidence for a direct report chain.",
            actor_type="employee",
            actor_ref="default",
        )
        deleted = await service.delete_document(
            profile_id="default",
            document_id=manager_doc.id,
            actor_type="employee",
            actor_ref="default",
            expected_revision=manager_doc.revision,
        )
        assert deleted.id == manager_doc.id

        with pytest.raises(TaskFlowServiceError) as flow_write_exc:
            await service.put_flow_document(
                profile_id="default",
                flow_id=flow.id,
                document_key="status",
                title="Project Status",
                body="A non-manager employee cannot mutate project-level memory.",
                actor_type="employee",
                actor_ref="auditor",
            )
        assert flow_write_exc.value.error_code == "task_actor_forbidden"

        flow_doc = await service.put_flow_document(
            profile_id="default",
            flow_id=flow.id,
            document_key="status",
            title="Project Status",
            body="Manager-owned project status update.",
            actor_type="employee",
            actor_ref="default",
        )
        assert flow_doc.updated_by_ref == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_builds_task_context_with_docs_history_and_relations(
    tmp_path: Path,
) -> None:
    """Task context should give agents the surrounding work state before execution."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_context.db",
    )
    service = TaskFlowService(factory)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Autonomous agent team",
            description="Make agents coordinate through tasks and docs.",
            created_by_type="human",
            created_by_ref="cli",
        )
        parent = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Implement collaboration loop",
            description="Build docs, context, mentions, and recovery primitives.",
            created_by_type="human",
            created_by_ref="cli",
        )
        delegated = await service.delegate_task(
            profile_id="default",
            source_task_id=parent.id,
            title="Draft context contract",
            description="Define the context bundle that every worker receives.",
            actor_type="employee",
            actor_ref="default",
            delegated_owner_type="employee",
            delegated_owner_ref="default",
            wait_for_delegated_task=True,
        )
        await service.add_task_comment(
            profile_id="default",
            task_id=parent.id,
            message="Initial plan persisted for the implementation run.",
            actor_type="employee",
            actor_ref="default",
            comment_type="plan",
        )
        task_doc = await service.put_task_document(
            profile_id="default",
            task_id=parent.id,
            document_key="handoff",
            title="Task handoff",
            body="Agents must receive docs, blockers, comments, and delegated work.",
            actor_type="employee",
            actor_ref="default",
        )
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            await repo.create_task_document(
                document_id="doc-legacy-roadmap",
                profile_id="default",
                scope_type="flow",
                scope_id=flow.id,
                document_key="roadmap",
                title="Legacy roadmap",
                body="This old flow doc must not enter task context.",
                created_by_type="human",
                created_by_ref="cli",
            )
            await repo.create_task_document(
                document_id="doc-legacy-task-spec",
                profile_id="default",
                scope_type="task",
                scope_id=parent.id,
                document_key="spec",
                title="Legacy task spec",
                body="This old task doc must not enter task context.",
                created_by_type="human",
                created_by_ref="cli",
            )

        context = await service.build_task_context(
            profile_id="default",
            task_id=parent.id,
            event_limit=10,
            comment_limit=10,
        )

        assert context.task.id == parent.id
        assert context.flow is not None
        assert context.flow.id == flow.id
        assert context.knowledge_packet is not None
        assert context.knowledge_packet.missing_flow_document_keys == ()
        assert [document.document_key for document in context.knowledge_packet.documents[:5]] == [
            "brief",
            "plan",
            "spec",
            "decisions",
            "status",
        ]
        assert any(document.document_key == "plan" for document in context.flow_documents)
        assert "roadmap" not in {document.document_key for document in context.flow_documents}
        assert context.task_documents == (task_doc,)
        assert "spec" not in {document.document_key for document in context.task_documents}
        assert "roadmap" not in {
            document.document_key for document in context.knowledge_packet.documents
        }
        assert not any(
            document.scope_type == "task" and document.document_key == "spec"
            for document in context.knowledge_packet.documents
        )
        assert context.recent_comments[0].comment_type == "plan"
        assert context.delegated_tasks[0].id == delegated.delegated_task.id
        assert context.dependencies[0].depends_on_task_id == delegated.delegated_task.id
    finally:
        await engine.dispose()


async def test_task_flow_service_mentions_show_up_in_employee_inbox(tmp_path: Path) -> None:
    """Task comments should turn @employee_id references into actionable employee inbox events."""

    db_name = "task_flow_agent_mentions.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Check competitor behavior",
            description="Compare our Task Flow behavior with Paperclip.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        await service.add_task_comment(
            profile_id="default",
            task_id=task.id,
            message="@researcher please verify the planning-doc assumptions.",
            actor_type="employee",
            actor_ref="default",
            comment_type="question",
        )

        inbox = await service.build_employee_inbox(
            profile_id="default",
            owner_type="employee",
            owner_ref="researcher",
            event_limit=10,
        )

        assert inbox.owner_type == "employee"
        assert inbox.owner_ref == "researcher"
        assert inbox.mention_event_count == 1
        assert {event.event_type for event in inbox.recent_events} >= {
            "mention_created",
            "wake_requested",
        }
        mention_event = next(
            event for event in inbox.recent_events if event.event_type == "mention_created"
        )
        wake_event = next(
            event for event in inbox.recent_events if event.event_type == "wake_requested"
        )
        assert mention_event.task_id == task.id
        assert wake_event.details["reason_code"] == "explicit_mention"
        assert mention_event.details["mentions"] == [
            {
                "owner_type": "employee",
                "owner_ref": "researcher",
                "token": "@researcher",
            }
        ]
    finally:
        await engine.dispose()


async def test_task_flow_service_employee_inbox_includes_review_assignments_by_reviewer(
    tmp_path: Path,
) -> None:
    """Employee inbox should match runtime claim ownership for review assignments."""

    db_name = "task_flow_agent_review_inbox.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="reviewer",
        markdown="# Reviewer\nReview assigned work.",
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        review_task = await service.create_task(
            profile_id="default",
            title="Review employee-owned implementation",
            description="Review this task as the assigned employee reviewer.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="reviewer",
        )
        await service.update_task(profile_id="default", task_id=review_task.id, status="review")

        inbox = await service.build_employee_inbox(
            profile_id="default",
            owner_type="employee",
            owner_ref="reviewer",
        )

        assert inbox.review_count == 1
        assert [item.id for item in inbox.tasks] == [review_task.id]

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed = await repo.claim_next_runnable_task(
                now_utc=datetime.now(timezone.utc),
                lease_until=datetime.now(timezone.utc) + timedelta(minutes=15),
                claim_token="claim-review-inbox",
                claimed_by="taskflow-runtime:reviewer",
            )
        assert claimed is not None
        assert claimed.id == review_task.id
        assert claimed.claim_owner_type == "employee"
        assert claimed.claim_owner_ref == "reviewer"

        active_inbox = await service.build_employee_inbox(
            profile_id="default",
            owner_type="employee",
            owner_ref="reviewer",
        )

        assert active_inbox.running_count == 1
        assert [item.id for item in active_inbox.tasks] == [review_task.id]

        review_inbox = await service.list_review_tasks(
            profile_id="default",
            actor_type="employee",
            actor_ref="reviewer",
        )
        assert [item.id for item in review_inbox] == [review_task.id]
        assert review_inbox[0].review_actionable is True
    finally:
        await engine.dispose()


async def test_task_flow_service_comment_on_review_task_wakes_reviewer_feed(
    tmp_path: Path,
) -> None:
    """Comments on review work should wake the reviewer, not just the task owner."""

    db_name = "task_flow_review_comment_wakes_reviewer.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Review implementation notes",
            description="Review the completed implementation notes.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="reviewer",
        )
        await service.update_task(profile_id="default", task_id=task.id, status="review")

        comment = await service.add_task_comment(
            profile_id="default",
            task_id=task.id,
            message="Please check the final evidence before approval.",
            actor_type="human",
            actor_ref="cli",
            comment_type="review_feedback",
        )

        inbox = await service.build_employee_inbox(
            profile_id="default",
            owner_type="employee",
            owner_ref="reviewer",
            event_limit=10,
        )

        wake_event = next(
            event
            for event in inbox.recent_events
            if event.event_type == "wake_requested"
            and event.details.get("reason_code") == "comment_added"
        )
        assert wake_event.task_id == task.id
        assert wake_event.details["owner_type"] == "employee"
        assert wake_event.details["owner_ref"] == "reviewer"
        assert wake_event.details["comment_id"] == comment.id
    finally:
        await engine.dispose()


async def test_task_flow_service_can_clear_reviewer_assignment(tmp_path: Path) -> None:
    """Explicit reviewer nulls should remove stale review routing."""

    db_name = "task_flow_clear_reviewer.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="reviewer",
        markdown="# Reviewer\nReview assigned work.",
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Clear stale reviewer",
            description="Remove the specialist reviewer and fall back to the task owner.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="default",
        )

        updated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            reviewer_type=None,
            reviewer_ref=None,
        )
        assert updated.reviewer_type is None
        assert updated.reviewer_ref is None

        review_task = await service.update_task(
            profile_id="default", task_id=task.id, status="review"
        )
        assert review_task.status == "review"
        assert review_task.reviewer_type is None
        assert review_task.reviewer_ref is None

        stale_reviewer_inbox = await service.build_employee_inbox(
            profile_id="default",
            owner_type="employee",
            owner_ref="reviewer",
        )
        assert stale_reviewer_inbox.total_count == 0

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claim_now = datetime.now(timezone.utc)
            stale_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-stale-reviewer",
                claimed_by="taskflow-runtime:stale-reviewer",
                owner_ref="reviewer",
            )
            owner_claim = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-owner-reviewer-fallback",
                claimed_by="taskflow-runtime:owner",
                owner_ref="default",
            )

        assert stale_claim is None
        assert owner_claim is not None
        assert owner_claim.id == task.id
        assert owner_claim.claim_owner_type == "employee"
        assert owner_claim.claim_owner_ref == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_records_wake_events_for_employee_dependency_unblocks(
    tmp_path: Path,
) -> None:
    """Employee-owned dependency-wait tasks should emit visible wake events when ready."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_employee_wake_events.db",
    )
    service = TaskFlowService(factory)
    try:
        prerequisite = await service.create_task(
            profile_id="default",
            title="Prepare API contract",
            description="Write the API contract before implementation starts.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        dependent = await service.create_task(
            profile_id="default",
            title="Implement API contract",
            description="Implement only after the contract is complete.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            depends_on_task_ids=(prerequisite.id,),
        )

        await service.update_task(
            profile_id="default",
            task_id=prerequisite.id,
            status="completed",
        )

        events = await service.list_task_events(profile_id="default", task_id=dependent.id)
        wake_event = next(event for event in events if event.event_type == "wake_requested")
        assert wake_event.details["reason_code"] == "dependencies_satisfied"
        assert wake_event.details["owner_type"] == "employee"
        assert wake_event.details["owner_ref"] == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_allows_assigning_task_to_employee(tmp_path: Path) -> None:
    """Tasks should accept direct employee ownership when the descriptor exists."""

    db_name = "task_flow_employee_owner.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="researcher",
        markdown="# Researcher\nHandle research tasks directly.",
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
        task = await service.create_task(
            profile_id="default",
            title="Subagent-owned task",
            description="Assign this directly to the analyst researcher subagent.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
            session_id="taskflow:subagent-direct",
        )

        assert task.owner_type == "employee"
        assert task.owner_ref == "researcher"
        assert task.last_session_id == "taskflow:subagent-direct"
        assert task.last_session_profile_id == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_accepts_employee_owner_for_profile_local_role(tmp_path: Path) -> None:
    """Public owner_type=employee should persist canonical employee ownership."""

    db_name = "task_flow_employee_owner.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_profile_subagent(
        settings=settings,
        profile_id="analyst",
        subagent_name="researcher",
        markdown="# Researcher\nHandle research tasks directly.",
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
        task = await service.create_task(
            profile_id="default",
            title="Employee-owned task",
            description="Assign this through the employee owner contract.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
        )

        assert task.owner_type == "employee"
        assert task.owner_ref == "researcher"
    finally:
        await engine.dispose()


async def test_task_flow_service_lists_stale_task_claims(tmp_path: Path) -> None:
    """Service should expose stale in-flight claims for operator maintenance flows."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_stale_claims.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Recover stale Employee task",
            description="Recover the stale Employee task after lease expiry.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        stale_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed = await repo.claim_next_runnable_task(
                now_utc=stale_now,
                lease_until=stale_now - timedelta(minutes=3),
                claim_token="stale-service-claim",
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
                session_id=f"taskflow:{task.id}",
                run_id=None,
                worker_id="taskflow-runtime:stale",
                started_at=stale_now - timedelta(minutes=5),
            )
            attached = await repo.attach_task_run(
                task_id=task.id,
                claim_token="stale-service-claim",
                task_run_id=task_run.id,
                session_id=f"taskflow:{task.id}",
            )
            assert attached is True
            started = await repo.mark_task_started(
                task_id=task.id,
                claim_token="stale-service-claim",
                started_at=stale_now - timedelta(minutes=5),
            )
            assert started is True

        stale_claims = await service.list_stale_task_claims(profile_id="default", limit=5)

        assert len(stale_claims) == 1
        stale_claim = stale_claims[0]
        assert stale_claim.task.id == task.id
        assert stale_claim.task.status == "running"
        assert stale_claim.claimed_by == "taskflow-runtime:stale"
        assert stale_claim.task.last_run_id is not None
        assert stale_claim.stale_for_sec >= 0
    finally:
        await engine.dispose()


async def test_task_flow_service_lists_stale_task_claims_for_one_owner_ref(tmp_path: Path) -> None:
    """Service should scope stale-claim maintenance views to one executor owner ref when requested."""

    db_name = "task_flow_stale_claims_owner_ref.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview stale claim ownership.",
        )
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("analyst", "papercliper"),
        )
        profile_task = await service.create_task(
            profile_id="default",
            title="Recover stale orchestrator task",
            description="Keep this stale claim outside the filtered result.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
        )
        subagent_task = await service.create_task(
            profile_id="default",
            title="Recover stale subagent task",
            description="Return only this filtered stale claim.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="reviewer",
        )
        stale_now = datetime.now(timezone.utc)

        async def _mark_stale(
            task_id: str, *, owner_ref: str, claim_token: str, worker_id: str
        ) -> None:
            async with session_scope(factory) as session:
                repo = TaskFlowRepository(session)
                claimed = await repo.claim_next_runnable_task(
                    now_utc=stale_now,
                    lease_until=stale_now - timedelta(minutes=3),
                    claim_token=claim_token,
                    claimed_by=worker_id,
                    profile_id="default",
                    owner_ref=owner_ref,
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
                    worker_id=worker_id,
                    started_at=stale_now - timedelta(minutes=5),
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
                    started_at=stale_now - timedelta(minutes=5),
                )
                assert started is True

        await _mark_stale(
            profile_task.id,
            owner_ref="analyst",
            claim_token="stale-profile-claim",
            worker_id="taskflow-runtime:profile",
        )
        await _mark_stale(
            subagent_task.id,
            owner_ref="reviewer",
            claim_token="stale-subagent-claim",
            worker_id="taskflow-runtime:subagent",
        )

        stale_claims = await service.list_stale_task_claims(
            profile_id="default",
            owner_ref="reviewer",
            limit=5,
        )

        assert len(stale_claims) == 1
        assert stale_claims[0].task.id == subagent_task.id
        assert stale_claims[0].task.owner_ref == "reviewer"
    finally:
        await engine.dispose()


async def test_task_flow_service_lists_review_inbox_with_reviewer_fallback(tmp_path: Path) -> None:
    """Review inbox should prefer reviewer assignment and fall back to owner when reviewer is unset."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_review_list.db",
    )
    service = TaskFlowService(factory)
    try:
        explicit_reviewer = await service.create_task(
            profile_id="default",
            title="Review Employee draft",
            description="Review the Employee draft response.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="default",
            labels=("review",),
        )
        await service.update_task(
            profile_id="default", task_id=explicit_reviewer.id, status="review"
        )

        owner_fallback = await service.create_task(
            profile_id="default",
            title="Owner fallback review",
            description="Review directly as the employee owner.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            labels=("review",),
        )
        await service.update_task(profile_id="default", task_id=owner_fallback.id, status="review")

        other_reviewer = await service.create_task(
            profile_id="default",
            title="Other reviewer task",
            description="Should not appear in the reviewer inbox.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="auditor",
            labels=("review",),
        )
        await service.update_task(profile_id="default", task_id=other_reviewer.id, status="review")

        inbox = await service.list_review_tasks(
            profile_id="default",
            actor_type="employee",
            actor_ref="default",
            labels=("review",),
        )

        assert {item.id for item in inbox} == {explicit_reviewer.id, owner_fallback.id}
    finally:
        await engine.dispose()


async def test_task_flow_service_lists_all_review_inboxes(tmp_path: Path) -> None:
    """Operator views should be able to discover review work across every reviewer."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_all_review_list.db",
    )
    service = TaskFlowService(factory)
    try:
        ai_review = await service.create_task(
            profile_id="default",
            title="Employee profile review",
            description="Review by the profile orchestrator.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="default",
            labels=("review",),
        )
        await service.update_task(profile_id="default", task_id=ai_review.id, status="review")
        reviewer_review = await service.create_task(
            profile_id="default",
            title="Specialist review",
            description="Review by a second employee reviewer.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="reviewer",
            labels=("review",),
        )
        await service.update_task(
            profile_id="default", task_id=reviewer_review.id, status="review"
        )
        running_work = await service.create_task(
            profile_id="default",
            title="Active implementation",
            description="Normal active work must not appear in review queues.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="default",
            labels=("review",),
        )
        await service.update_task(
            profile_id="default",
            task_id=running_work.id,
            status="running",
            session_id="session-active-implementation",
        )

        all_review = await service.list_review_tasks(profile_id="default", labels=("review",))
        ai_inbox = await service.list_review_tasks(
            profile_id="default",
            actor_type="employee",
            actor_ref="default",
            labels=("review",),
        )

        assert {item.id for item in all_review} == {ai_review.id, reviewer_review.id}
        assert [item.id for item in ai_inbox] == [ai_review.id]
    finally:
        await engine.dispose()


async def test_task_flow_repository_claims_review_task_with_blank_reviewer_via_owner(
    tmp_path: Path,
) -> None:
    """Review claim fallback should treat empty reviewer fields like missing reviewer fields."""

    db_name = "task_flow_review_blank_reviewer_claim.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_profile_subagent(
        settings=settings,
        profile_id="default",
        subagent_name="researcher",
        markdown="# Researcher\nHandle owner fallback review claims.",
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Owner fallback Employee review claim",
            description="A review task with blank reviewer fields should still be claimable by the Employee owner.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="researcher",
            reviewer_type="",
            reviewer_ref="",
        )
        await service.update_task(profile_id="default", task_id=task.id, status="review")

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claim_now = datetime.now(timezone.utc)
            claimed = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-review-owner-fallback",
                claimed_by="taskflow-runtime:reviewer",
            )

        assert claimed is not None
        assert claimed.id == task.id
        assert claimed.claim_owner_type == "employee"
        assert claimed.claim_owner_ref == "researcher"
        assert claimed.claim_source_status == "review"
    finally:
        await engine.dispose()


async def test_task_flow_service_review_actions_transition_tasks_and_unblock_dependents(
    tmp_path: Path,
) -> None:
    """Approve/request-changes actions should enforce review semantics and dependency behavior."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_review_actions.db",
    )
    service = TaskFlowService(factory)
    try:
        review_task = await service.create_task(
            profile_id="default",
            title="Review release draft",
            description="Review and approve the release draft.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="reviewer",
        )
        await service.update_task(profile_id="default", task_id=review_task.id, status="review")

        dependent = await service.create_task(
            profile_id="default",
            title="Publish after approval",
            description="Publish only after review completes.",
            created_by_type="human",
            created_by_ref="cli",
            depends_on_task_ids=(review_task.id,),
        )
        assert dependent.status == "blocked"

        with pytest.raises(TaskFlowServiceError, match="selected actor"):
            await service.approve_review_task(
                profile_id="default",
                task_id=review_task.id,
                actor_type="employee",
                actor_ref="auditor",
            )

        approved = await service.approve_review_task(
            profile_id="default",
            task_id=review_task.id,
            actor_type="employee",
            actor_ref="reviewer",
        )
        assert approved.status == "completed"

        unblocked = await service.get_task(profile_id="default", task_id=dependent.id)
        assert unblocked.status == "todo"

        second_review = await service.create_task(
            profile_id="default",
            title="Review revision request",
            description="Return this task for changes.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        await service.update_task(profile_id="default", task_id=second_review.id, status="review")

        changed = await service.request_review_changes(
            profile_id="default",
            task_id=second_review.id,
            actor_type="employee",
            actor_ref="default",
            owner_type="employee",
            owner_ref="default",
            reason_text="Needs source citations before approval.",
        )
        assert changed.status == "blocked"
        assert changed.owner_type == "employee"
        assert changed.owner_ref == "default"
        assert changed.blocked_reason_code == "review_changes_requested"
        assert changed.blocked_reason_text == "Needs source citations before approval."
    finally:
        await engine.dispose()


async def test_task_flow_service_review_actions_accept_employee_actor(tmp_path: Path) -> None:
    """Review inbox, approval, and changes should accept employee actor identity."""

    db_name = "task_flow_review_employee_actor.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_profile_subagent(
        settings=settings,
        profile_id="papercliper",
        subagent_name="reviewer",
        markdown="# Reviewer\nReview specialist.",
    )
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("papercliper",),
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "papercliper"),
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        approval_task = await service.create_task(
            profile_id="default",
            title="Subagent review approval",
            description="Approve this through the employee actor contract.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="reviewer",
        )
        await service.update_task(profile_id="default", task_id=approval_task.id, status="review")

        inbox = await service.list_review_tasks(
            profile_id="default",
            actor_type="employee",
            actor_ref="reviewer",
        )
        assert [item.id for item in inbox] == [approval_task.id]

        approved = await service.approve_review_task(
            profile_id="default",
            task_id=approval_task.id,
            actor_type="employee",
            actor_ref="reviewer",
        )
        assert approved.status == "completed"

        changes_task = await service.create_task(
            profile_id="default",
            title="Subagent review changes",
            description="Request changes through the employee actor contract.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="reviewer",
        )
        await service.update_task(profile_id="default", task_id=changes_task.id, status="review")

        changed = await service.request_review_changes(
            profile_id="default",
            task_id=changes_task.id,
            actor_type="employee",
            actor_ref="reviewer",
            reason_text="Route this back to the reviewer specialist.",
        )
        assert changed.status == "blocked"
        assert changed.owner_type == "employee"
        assert changed.owner_ref == "reviewer"
        assert changed.ready_at is not None

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claim_now = datetime.now(timezone.utc)
            claimed = await repo.claim_next_runnable_task(
                now_utc=claim_now,
                lease_until=claim_now + timedelta(minutes=15),
                claim_token="claim-review-changes",
                claimed_by="taskflow-runtime:reviewer",
            )
        assert claimed is not None
        assert claimed.id == changes_task.id
        assert claimed.claim_owner_type == "employee"
        assert claimed.claim_owner_ref == "reviewer"
    finally:
        await engine.dispose()


async def test_task_flow_service_request_review_changes_respects_team_roster(
    tmp_path: Path,
) -> None:
    """Employee reviewers should not be able to bounce reviewed work to arbitrary Employee profiles."""

    db_name = "task_flow_review_assignment_guard.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "papercliper", "outsider"),
    )
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("papercliper",),
    )
    await _create_chat_session(
        factory,
        profile_id="papercliper",
        session_id="taskflow:papercliper-review",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        review_task = await service.create_task(
            profile_id="default",
            title="Employee reviewed task",
            description="Review and send back if changes are needed.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="papercliper",
        )
        await service.update_task(
            profile_id="default",
            task_id=review_task.id,
            status="review",
            actor_type="human",
            actor_ref="cli",
        )

        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.request_review_changes(
                profile_id="default",
                task_id=review_task.id,
                actor_type="employee",
                actor_ref="papercliper",
                actor_session_id="taskflow:papercliper-review",
                owner_type="employee",
                owner_ref="outsider",
                reason_text="Reassigning outside the configured team should be blocked.",
            )

        assert exc_info.value.error_code == "task_owner_forbidden"
    finally:
        await engine.dispose()


async def test_task_flow_service_manages_dependency_edges_and_rejects_cycles(
    tmp_path: Path,
) -> None:
    """Dependencies should be listable/removable and reject cyclic graphs."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_dependencies.db",
    )
    service = TaskFlowService(factory)
    try:
        root = await service.create_task(
            profile_id="default",
            title="Root task",
            description="Finish the upstream prerequisite.",
            created_by_type="human",
            created_by_ref="cli",
        )
        leaf = await service.create_task(
            profile_id="default",
            title="Leaf task",
            description="Run after the root task.",
            created_by_type="human",
            created_by_ref="cli",
        )

        edge = await service.add_dependency(
            profile_id="default",
            task_id=leaf.id,
            depends_on_task_id=root.id,
        )
        assert edge.task_id == leaf.id
        assert edge.depends_on_task_id == root.id
        assert edge.satisfied_on_status == "completed"

        listed = await service.list_dependencies(profile_id="default", task_id=leaf.id)
        assert [(item.task_id, item.depends_on_task_id) for item in listed] == [(leaf.id, root.id)]

        blocked_leaf = await service.get_task(profile_id="default", task_id=leaf.id)
        assert blocked_leaf.status == "blocked"
        assert blocked_leaf.blocked_reason_code == "dependency_wait"

        with pytest.raises(TaskFlowServiceError, match="cycle"):
            await service.add_dependency(
                profile_id="default",
                task_id=root.id,
                depends_on_task_id=leaf.id,
            )

        deleted = await service.remove_dependency(
            profile_id="default",
            task_id=leaf.id,
            depends_on_task_id=root.id,
        )
        assert deleted is True
        unblocked_leaf = await service.get_task(profile_id="default", task_id=leaf.id)
        assert unblocked_leaf.status == "todo"
        assert unblocked_leaf.blocked_reason_code is None
        assert unblocked_leaf.depends_on_task_ids == ()
        assert await service.list_dependencies(profile_id="default", task_id=leaf.id) == []
    finally:
        await engine.dispose()


async def test_task_flow_service_builds_board_with_counts_and_filters(tmp_path: Path) -> None:
    """Board should expose stable column counts, overdue signals, and limited previews."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_board.db",
        profile_ids=("default", "papercliper"),
    )
    service = TaskFlowService(factory)
    now_utc = datetime.now(timezone.utc)
    try:
        prereq = await service.create_task(
            profile_id="default",
            title="Prerequisite",
            description="Complete prerequisite work first.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        todo_task = await service.create_task(
            profile_id="default",
            title="Todo overdue",
            description="Finish the overdue item.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            priority=90,
            due_at=now_utc - timedelta(hours=2),
            labels=("release",),
        )
        await service.create_task(
            profile_id="default",
            title="Blocked downstream",
            description="Wait for prerequisite completion.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            depends_on_task_ids=(prereq.id,),
            labels=("release",),
        )
        review_task = await service.create_task(
            profile_id="default",
            title="Needs human review",
            description="Wait for review before completion.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="reviewer",
            labels=("release",),
        )
        await service.update_task(profile_id="default", task_id=review_task.id, status="review")
        completed_task = await service.create_task(
            profile_id="default",
            title="Completed item",
            description="Already completed.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            labels=("release",),
        )
        await service.update_task(
            profile_id="default", task_id=completed_task.id, status="completed"
        )
        failed_task = await service.create_task(
            profile_id="default",
            title="Failed item",
            description="Already failed.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            labels=("release",),
        )
        await service.update_task(profile_id="default", task_id=failed_task.id, status="failed")
        cancelled_task = await service.create_task(
            profile_id="default",
            title="Cancelled item",
            description="Already cancelled.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            labels=("release",),
        )
        await service.update_task(
            profile_id="default", task_id=cancelled_task.id, status="cancelled"
        )
        await service.create_task(
            profile_id="default",
            title="Claimed item",
            description="Claimed by background worker.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            priority=95,
            labels=("release",),
        )
        running_task = await service.create_task(
            profile_id="default",
            title="Running item",
            description="Running in background.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="papercliper",
            priority=55,
            labels=("release",),
        )
        claim_now_utc = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed_row = await repo.claim_next_runnable_task(
                now_utc=claim_now_utc,
                lease_until=claim_now_utc + timedelta(minutes=15),
                claim_token="claim-board-1",
                claimed_by="taskflow-runtime:0",
            )
            assert claimed_row is not None
            second_row = await repo.claim_next_runnable_task(
                now_utc=claim_now_utc,
                lease_until=claim_now_utc + timedelta(minutes=15),
                claim_token="claim-board-2",
                claimed_by="taskflow-runtime:0",
            )
            assert second_row is not None
            running_row = claimed_row if claimed_row.id == running_task.id else second_row
            await repo.mark_task_started(
                task_id=running_row.id,
                claim_token="claim-board-1"
                if running_row.id == claimed_row.id
                else "claim-board-2",
                started_at=claim_now_utc,
            )

        board = await service.build_board(
            profile_id="default",
            labels=("release",),
            limit_per_column=1,
        )

        columns = {column.id: column for column in board.columns}
        assert board.total_count == 8
        assert board.ready_count == 1
        assert board.blocked_count == 1
        assert board.running_count == 2
        assert board.needs_review_count == 1
        assert board.overdue_count == 1
        assert columns["todo"].count == 1
        assert columns["blocked"].count == 1
        assert columns["running"].count == 2
        assert len(columns["running"].tasks) == 1
        assert columns["review"].count == 1
        assert columns["completed"].count == 1
        assert columns["failed"].count == 1
        assert columns["cancelled"].count == 1
        assert columns["todo"].tasks[0].id == todo_task.id
    finally:
        await engine.dispose()


async def test_task_flow_service_reports_live_task_session_activity(tmp_path: Path) -> None:
    """Running tasks should expose the bound live session and aggregate queued webhook turns."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_session_activity.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Process webhook batch",
            description="Handle the inbound webhook payloads in order.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        updated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="running",
            session_id="taskflow:manual-session",
        )
        assert updated.last_session_id == "taskflow:manual-session"

        touched_at = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            queue_repo = ChatSessionTurnQueueRepository(session)
            running_marker = await queue_repo.enqueue(
                profile_id="default",
                session_id="taskflow:manual-session",
                owner_token="queue-owner-1",
                source="taskflow",
                client_msg_id="msg-1",
            )
            started = await queue_repo.try_mark_running(
                queue_item_id=running_marker.id,
                profile_id="default",
                session_id="taskflow:manual-session",
                owner_token="queue-owner-1",
                touched_at=touched_at,
            )
            assert started is True
            await queue_repo.enqueue(
                profile_id="default",
                session_id="taskflow:manual-session",
                owner_token="queue-owner-2",
                source="webhook",
                client_msg_id="msg-2",
            )

        refreshed = await service.get_task(profile_id="default", task_id=task.id)
        assert refreshed.active_session is not None
        assert refreshed.active_session.session_id == "taskflow:manual-session"
        assert refreshed.active_session.session_profile_id == "default"
        assert refreshed.active_session.dialog_active is True
        assert refreshed.active_session.running_turn_count == 1
        assert refreshed.active_session.queued_turn_count == 1

        activity = await service.list_task_session_activity(
            profile_id="default",
            task_ids=(task.id,),
        )
        assert activity[task.id].running_turn_count == 1
        assert activity[task.id].queued_turn_count == 1

        board = await service.build_board(profile_id="default")
        running_column = next(column for column in board.columns if column.id == "running")
        board_task = next(item for item in running_column.tasks if item.id == task.id)
        assert board_task.active_session is not None
        assert board_task.active_session.session_id == "taskflow:manual-session"
    finally:
        await engine.dispose()


async def test_task_flow_service_uses_persisted_session_profile_for_activity_lookup(
    tmp_path: Path,
) -> None:
    """Live session binding should stay exact even when execution profile differs from task owner."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_session_profile_activity.db",
        profile_ids=("default", "papercliper"),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Run in delegated session",
            description="Track a session owned by another execution profile.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        updated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="running",
            session_id="main",
            session_profile_id="papercliper",
        )
        assert updated.last_session_id == "main"
        assert updated.last_session_profile_id == "papercliper"

        touched_at = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            queue_repo = ChatSessionTurnQueueRepository(session)
            running_marker = await queue_repo.enqueue(
                profile_id="papercliper",
                session_id="main",
                owner_token="delegate-owner-1",
                source="taskflow",
                client_msg_id="delegate-msg-1",
            )
            started = await queue_repo.try_mark_running(
                queue_item_id=running_marker.id,
                profile_id="papercliper",
                session_id="main",
                owner_token="delegate-owner-1",
                touched_at=touched_at,
            )
            assert started is True

        refreshed = await service.get_task(profile_id="default", task_id=task.id)
        assert refreshed.active_session is not None
        assert refreshed.active_session.session_id == "main"
        assert refreshed.active_session.session_profile_id == "papercliper"
        assert refreshed.active_session.running_turn_count == 1
    finally:
        await engine.dispose()


async def test_task_flow_service_rebinding_session_reinfers_profile_from_owner(
    tmp_path: Path,
) -> None:
    """Explicitly rebinding a session should not stay pinned to an old execution profile."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_session_profile_rebind.db",
        profile_ids=("default", "papercliper"),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Rebind live session",
            description="Move the task onto a new session binding.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        delegated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="running",
            session_id="main",
            session_profile_id="papercliper",
        )
        assert delegated.last_session_profile_id == "papercliper"

        rebound = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="running",
            session_id="main-2",
        )
        assert rebound.last_session_id == "main-2"
        assert rebound.last_session_profile_id == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_create_task_rejects_missing_description(tmp_path: Path) -> None:
    """create_task should fail fast when description is omitted."""

    engine, factory = await build_repository_factory(
        tmp_path, db_name="task_flow_create_requires_description.db"
    )
    service = TaskFlowService(factory)
    try:
        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.create_task(
                profile_id="default",
                title="Missing description",
                description=None,
                created_by_type="human",
                created_by_ref="cli",
            )

        assert exc_info.value.error_code == "invalid_description"
        assert exc_info.value.reason == "description is required"
    finally:
        await engine.dispose()


async def test_task_flow_service_delegate_task_rejects_missing_description(tmp_path: Path) -> None:
    """delegate_task should fail fast when description is omitted."""

    engine, factory = await build_repository_factory(
        tmp_path, db_name="task_flow_delegate_requires_description.db"
    )
    service = TaskFlowService(factory)
    try:
        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.delegate_task(
                profile_id="default",
                source_task_id="task_source",
                delegated_owner_ref="reviewer",
                description=None,
                actor_type="employee",
                actor_ref="default",
            )

        assert exc_info.value.error_code == "invalid_description"
        assert exc_info.value.reason == "description is required"
    finally:
        await engine.dispose()


async def test_task_flow_service_delegate_task_creates_handoff_and_dependency(
    tmp_path: Path,
) -> None:
    """Delegation should create a child Employee task and block the source task on it when requested."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_delegate_task.db",
        profile_ids=("default", "analyst", "papercliper"),
    )
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name="task_flow_delegate_task.db")
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst", "papercliper"),
    )
    await _create_chat_session(
        factory,
        profile_id="analyst",
        session_id="taskflow:analyst-delegate",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        source_task = await service.create_task(
            profile_id="default",
            title="Prepare launch brief",
            description="Own the main launch brief and delegate research when needed.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
            flow_id=None,
            labels=("launch", "brief"),
        )

        delegation = await service.delegate_task(
            profile_id="default",
            source_task_id=source_task.id,
            delegated_owner_ref="papercliper",
            description="Research competitor messaging and deliver a concise summary.",
            actor_type="employee",
            actor_ref="analyst",
            actor_session_id="taskflow:analyst-delegate",
            wait_for_delegated_task=True,
        )

        assert delegation.source_task.id == source_task.id
        assert delegation.source_task.status == "blocked"
        assert delegation.source_task.blocked_reason_code == "dependency_wait"
        assert delegation.delegated_task.owner_type == "employee"
        assert delegation.delegated_task.owner_ref == "papercliper"
        assert delegation.delegated_task.source_type == "task_delegation"
        assert delegation.delegated_task.source_ref == source_task.id
        assert delegation.delegated_task.created_by_type == "employee"
        assert delegation.delegated_task.created_by_ref == "analyst"
        assert delegation.dependency is not None
        assert delegation.dependency.task_id == source_task.id
        assert delegation.dependency.depends_on_task_id == delegation.delegated_task.id

        comments = await service.list_task_comments(profile_id="default", task_id=source_task.id)
        assert comments[0].comment_type == "delegation"
        assert delegation.delegated_task.id in comments[0].message
    finally:
        await engine.dispose()


async def test_task_flow_service_delegate_task_supports_employee_owner(
    tmp_path: Path,
) -> None:
    """Delegation should support direct employee assignments without proxy profiles."""

    db_name = "task_flow_delegate_employee.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_profile_subagent(
        settings=settings,
        profile_id="papercliper",
        subagent_name="researcher",
        markdown="# Researcher\nInvestigate and report.",
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst", "papercliper", "outsider"),
    )
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst", "papercliper"),
    )
    await _create_chat_session(
        factory,
        profile_id="analyst",
        session_id="taskflow:analyst-subagent-delegate",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        source_task = await service.create_task(
            profile_id="default",
            title="Own launch brief",
            description="Prepare the launch brief and delegate targeted research.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
        )

        delegation = await service.delegate_task(
            profile_id="default",
            source_task_id=source_task.id,
            delegated_owner_type="employee",
            delegated_owner_ref="researcher",
            description="Research competitors and summarize gaps.",
            actor_type="employee",
            actor_ref="analyst",
            actor_session_id="taskflow:analyst-subagent-delegate",
            wait_for_delegated_task=True,
        )

        assert delegation.source_task.status == "blocked"
        assert delegation.source_task.blocked_reason_code == "dependency_wait"
        assert delegation.delegated_task.owner_type == "employee"
        assert delegation.delegated_task.owner_ref == "researcher"
        assert delegation.delegated_task.created_by_type == "employee"
        assert delegation.delegated_task.created_by_ref == "analyst"
        assert delegation.dependency.depends_on_task_id == delegation.delegated_task.id
    finally:
        await engine.dispose()


async def test_task_flow_service_derives_operator_friendly_block_state(tmp_path: Path) -> None:
    """Task metadata should expose normalized block-state hints for UI/runtime consumers."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_block_state.db",
        profile_ids=("default", "papercliper"),
    )
    service = TaskFlowService(factory)
    try:
        scheduled_task = await service.create_task(
            profile_id="default",
            title="Check vendor status",
            description="Wait for the external vendor and revisit later.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        scheduled_ready_at = datetime.now(timezone.utc) + timedelta(hours=2)
        scheduled_task = await service.block_task(
            profile_id="default",
            task_id=scheduled_task.id,
            reason_code="vendor_pending",
            reason_text="The external vendor has not replied yet.",
            actor_type="employee",
            actor_ref="default",
            ready_at=scheduled_ready_at,
        )
        assert scheduled_task.block_state is not None
        assert scheduled_task.block_state.kind == "scheduled_retry"
        assert scheduled_task.block_state.retry_scheduled is True
        assert scheduled_task.block_state.ready_at == scheduled_task.ready_at

        human_wait_task = await service.create_task(
            profile_id="default",
            title="Need human approval",
            description="Wait for a human decision before continuing.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        human_wait_task = await service.block_task(
            profile_id="default",
            task_id=human_wait_task.id,
            reason_code="awaiting_human_input",
            reason_text="Need approval from Alice.",
            actor_type="employee",
            actor_ref="default",
        )
        assert human_wait_task.block_state is not None
        assert human_wait_task.block_state.kind == "human_wait"
        assert human_wait_task.block_state.waiting_for_human is True

        review_task = await service.create_task(
            profile_id="default",
            title="Prepare human review",
            description="Route the final answer to the reviewer.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="reviewer",
        )
        review_task = await service.update_task(
            profile_id="default",
            task_id=review_task.id,
            status="review",
            blocked_reason_code="awaiting_human_review",
            blocked_reason_text="Ready for Alice to review.",
        )
        assert review_task.block_state is not None
        assert review_task.block_state.kind == "review"
        assert review_task.block_state.waiting_for_human is True

        ai_review_task = await service.create_task(
            profile_id="default",
            title="Prepare Employee review",
            description="Route the draft to another Employee reviewer.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="papercliper",
        )
        ai_review_task = await service.update_task(
            profile_id="default",
            task_id=ai_review_task.id,
            status="review",
            blocked_reason_code="awaiting_ai_review",
            blocked_reason_text="Ready for Papercliper review.",
        )
        assert ai_review_task.block_state is not None
        assert ai_review_task.block_state.kind == "review"
        assert ai_review_task.block_state.waiting_for_human is False

        prerequisite_task = await service.create_task(
            profile_id="default",
            title="Complete prerequisite",
            description="Finish the prerequisite work first.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        dependent_task = await service.create_task(
            profile_id="default",
            title="Wait on prerequisite",
            description="Continue only after the prerequisite is done.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            depends_on_task_ids=(prerequisite_task.id,),
        )
        assert dependent_task.block_state is not None
        assert dependent_task.block_state.kind == "dependency_wait"
        assert dependent_task.block_state.waiting_for_dependency is True
        assert dependent_task.block_state.depends_on_task_ids == (prerequisite_task.id,)

        await service.update_task(
            profile_id="default",
            task_id=prerequisite_task.id,
            status="completed",
        )
        vendor_blocked_task = await service.block_task(
            profile_id="default",
            task_id=dependent_task.id,
            reason_code="vendor_pending",
            reason_text="Still waiting on the external vendor.",
            actor_type="employee",
            actor_ref="default",
        )
        assert vendor_blocked_task.block_state is not None
        assert vendor_blocked_task.block_state.kind == "blocked"
        assert vendor_blocked_task.block_state.waiting_for_dependency is False
        assert vendor_blocked_task.block_state.depends_on_task_ids == (prerequisite_task.id,)
    finally:
        await engine.dispose()

async def test_task_flow_service_rejects_ai_actor_mutating_coworker_task(tmp_path: Path) -> None:
    """Employee workers should only mutate their own tasks unless they are the backlog manager."""

    db_name = "task_flow_actor_permissions.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst", "papercliper"),
    )
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst", "papercliper"),
    )
    await _create_chat_session(
        factory,
        profile_id="default",
        session_id="taskflow:outsider-demo",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        coworker_task = await service.create_task(
            profile_id="default",
            title="Papercliper owned task",
            description="Own this task as Papercliper.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="papercliper",
        )

        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.update_task(
                profile_id="default",
                task_id=coworker_task.id,
                status="running",
                actor_type="employee",
                actor_ref="outsider",
                actor_session_id="taskflow:outsider-demo",
                session_id="taskflow:outsider-demo",
            )

        assert exc_info.value.error_code == "task_actor_forbidden"
    finally:
        await engine.dispose()


async def test_task_flow_service_enforces_public_principal_when_flag_enabled(
    tmp_path: Path,
) -> None:
    """Enabled public principal guard should reject mutating calls without actor identity."""

    db_name = "task_flow_public_principal_flag_enabled.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Public owner baseline",
            description="Create a task for public-principal enforcement checks.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        with pytest.raises(TaskFlowServiceError) as update_exc:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="running",
                actor_type="human",
                actor_ref="someone_else",
            )
        assert update_exc.value.error_code == "task_actor_required"
    finally:
        await engine.dispose()


async def test_task_flow_service_requires_public_human_intake_on_root_employee(
    tmp_path: Path,
) -> None:
    """Public human task intake should enter through the root employee only."""

    db_name = "task_flow_public_human_root_intake.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        with pytest.raises(TaskFlowServiceError) as direct_worker_exc:
            await service.create_task(
                profile_id="default",
                title="Bypass CTO",
                description="A human should not assign intake directly to a specialist.",
                created_by_type="human",
                created_by_ref="cli",
                owner_type="employee",
                owner_ref="reviewer",
            )
        assert direct_worker_exc.value.error_code == "task_intake_root_required"

        with pytest.raises(TaskFlowServiceError) as human_default_exc:
            await service.create_flow(
                profile_id="default",
                title="Human default owner",
                description="Flow defaults cannot bypass employee intake.",
                created_by_type="human",
                created_by_ref="cli",
                default_owner_type="human",
                default_owner_ref="cli",
            )
        assert human_default_exc.value.error_code == "invalid_owner_type"

        explicit_root = await service.create_task(
            profile_id="default",
            title="Explicit CTO intake",
            description="Route the human intake to the root employee.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        assert explicit_root.owner_type == "employee"
        assert explicit_root.owner_ref == "default"

        implicit_root = await service.create_task(
            profile_id="default",
            title="Implicit CTO intake",
            description="Default owner resolution should also use the root employee.",
            created_by_type="human",
            created_by_ref="cli",
        )
        assert implicit_root.owner_type == "employee"
        assert implicit_root.owner_ref == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_requires_actor_identity_for_public_deletes(
    tmp_path: Path,
) -> None:
    """Public delete operations should require the same principal proof as other mutations."""

    db_name = "task_flow_public_delete_actor_required.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        flow = await service.create_flow(
            profile_id="default",
            title="Delete guard flow",
            description="Flow delete should require an operator actor.",
            created_by_type="human",
            created_by_ref="cli",
        )
        task = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Delete guard task",
            description="Task delete should require an operator actor.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )

        with pytest.raises(TaskFlowServiceError) as task_delete_exc:
            await service.delete_task(profile_id="default", task_id=task.id)
        assert task_delete_exc.value.error_code == "task_actor_required"

        with pytest.raises(TaskFlowServiceError) as flow_delete_exc:
            await service.delete_flow(profile_id="default", flow_id=flow.id)
        assert flow_delete_exc.value.error_code == "task_actor_required"

        await service.delete_task(
            profile_id="default",
            task_id=task.id,
            actor_type="human",
            actor_ref="cli",
        )
        await service.delete_flow(
            profile_id="default",
            flow_id=flow.id,
            actor_type="human",
            actor_ref="cli",
        )
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_public_human_session_binding(
    tmp_path: Path,
) -> None:
    """Human/API callers must not attach arbitrary chat sessions to public tasks."""

    db_name = "task_flow_public_human_session_binding.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "other"),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        with pytest.raises(TaskFlowServiceError) as create_exc:
            await service.create_task(
                profile_id="default",
                title="Spoof session on create",
                description="Creation must not bind a human supplied session.",
                created_by_type="human",
                created_by_ref="cli",
                owner_type="employee",
                owner_ref="default",
                session_id="borrowed-session",
                session_profile_id="other",
            )
        assert create_exc.value.error_code == "task_session_binding_forbidden"

        task = await service.create_task(
            profile_id="default",
            title="Spoof session on update",
            description="Update must not bind a human supplied session.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        with pytest.raises(TaskFlowServiceError) as update_exc:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="running",
                actor_type="human",
                actor_ref="cli",
                session_id="borrowed-session",
                session_profile_id="other",
            )
        assert update_exc.value.error_code == "task_session_binding_forbidden"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_public_employee_actor_without_live_session(
    tmp_path: Path,
) -> None:
    """Employee public actors should require a live session instead of trusting actor_ref."""

    db_name = "task_flow_public_employee_session_required.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Employee spoof target",
            description="A plain employee ref must not be enough to mutate.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="default",
        )

        with pytest.raises(TaskFlowServiceError) as update_exc:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="blocked",
                actor_type="employee",
                actor_ref="default",
                blocked_reason_code="spoof",
                blocked_reason_text="No live session proof.",
            )
        assert update_exc.value.error_code == "task_actor_required"

        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="review",
            actor_type="human",
            actor_ref="cli",
        )
        with pytest.raises(TaskFlowServiceError) as approve_exc:
            await service.approve_review_task(
                profile_id="default",
                task_id=task.id,
                actor_type="employee",
                actor_ref="default",
            )
        assert approve_exc.value.error_code == "task_review_actor_required"
    finally:
        await engine.dispose()


async def test_task_flow_service_allows_automation_creator_on_matching_backlog_under_public_principal(
    tmp_path: Path,
) -> None:
    """Automation actors should create tasks in their own backlog without spoofing chat sessions."""

    db_name = "task_flow_public_automation_creator.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        actor_ref = await _create_automation_actor(
            factory,
            profile_id="default",
            name="matching-backlog-automation",
        )
        task = await service.create_task(
            profile_id="default",
            title="Automation created task",
            description="Created from webhook automation without a live chat session.",
            created_by_type="automation",
            created_by_ref=actor_ref,
            owner_type="employee",
            owner_ref="default",
        )

        assert task.created_by_type == "automation"
        assert task.created_by_ref == actor_ref
        assert task.owner_type == "employee"
        assert task.owner_ref == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_automation_creator_for_other_backlog_under_public_principal(
    tmp_path: Path,
) -> None:
    """Automation actors must stay scoped to the backlog encoded in their principal ref."""

    db_name = "task_flow_public_automation_creator_scope.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        actor_ref = await _create_automation_actor(
            factory,
            profile_id="analyst",
            name="cross-backlog-automation",
        )
        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.create_task(
                profile_id="default",
                title="Cross backlog automation task",
                description="Should fail because the automation principal belongs to another backlog.",
                created_by_type="automation",
                created_by_ref=actor_ref,
                owner_type="employee",
                owner_ref="default",
            )

        assert exc_info.value.error_code == "task_creator_forbidden"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_spoofed_automation_creator_under_public_principal(
    tmp_path: Path,
) -> None:
    """Automation actors must resolve to one real persisted automation record."""

    db_name = "task_flow_public_automation_creator_spoof.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.create_task(
                profile_id="default",
                title="Spoofed automation task",
                description="Should fail because no such automation principal exists.",
                created_by_type="automation",
                created_by_ref="automation:default:424242",
                owner_type="employee",
                owner_ref="default",
            )

        assert exc_info.value.error_code == "automation_not_found"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_spoofed_automation_actor_on_task_mutation(
    tmp_path: Path,
) -> None:
    """Mutation paths must also anchor automation actors to real automation rows."""

    db_name = "task_flow_public_automation_mutation_spoof.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Seed task",
            description="Used to verify automation mutation anchoring.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )

        with pytest.raises(TaskFlowServiceError) as update_exc:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="blocked",
                blocked_reason_code="automation_spoof",
                blocked_reason_text="should fail",
                actor_type="automation",
                actor_ref="automation:default:424242",
            )
        assert update_exc.value.error_code == "automation_not_found"

        with pytest.raises(TaskFlowServiceError) as comment_exc:
            await service.add_task_comment(
                profile_id="default",
                task_id=task.id,
                message="spoofed automation comment",
                actor_type="automation",
                actor_ref="automation:default:424242",
            )
        assert comment_exc.value.error_code == "automation_not_found"
    finally:
        await engine.dispose()


async def test_task_flow_service_limits_automation_mutations_to_own_tasks(
    tmp_path: Path,
) -> None:
    """One automation must not get backlog-wide write authority over unrelated tasks."""

    db_name = "task_flow_public_automation_mutation_scope.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default",),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        actor_ref = await _create_automation_actor(
            factory,
            profile_id="default",
            name="scoped-automation",
        )
        task = await service.create_task(
            profile_id="default",
            title="Human task",
            description="Automation should not manage this backlog item.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )

        with pytest.raises(TaskFlowServiceError) as update_exc:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="blocked",
                blocked_reason_code="automation_scope",
                blocked_reason_text="should fail",
                actor_type="automation",
                actor_ref=actor_ref,
            )
        assert update_exc.value.error_code == "task_actor_forbidden"
    finally:
        await engine.dispose()


async def test_task_flow_service_requires_actor_identity_on_public_mutations(
    tmp_path: Path,
) -> None:
    """Public service instances should reject anonymous task and review mutations."""

    db_name = "task_flow_public_actor_required.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "papercliper"),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_public_principal_required=True,
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Public mutation target",
            description="Require an explicit actor for public mutations.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
            reviewer_type="employee",
            reviewer_ref="papercliper",
        )
        dependency_root = await service.create_task(
            profile_id="default",
            title="Dependency root",
            description="Use this to test dependency mutation authorization.",
            created_by_type="human",
            created_by_ref="cli",
        )

        with pytest.raises(TaskFlowServiceError) as update_exc:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="running",
                session_id="papercliper-main",
            )
        assert update_exc.value.error_code == "task_actor_required"

        await _create_chat_session(
            factory,
            profile_id="papercliper",
            session_id="papercliper-main",
        )
        with pytest.raises(TaskFlowServiceError) as ai_update_exc:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="running",
                actor_type="employee",
                actor_ref="default",
                actor_session_id="papercliper-main",
                session_id="papercliper-main",
            )
        assert ai_update_exc.value.error_code == "task_actor_required"

        await _create_chat_session(
            factory,
            profile_id="default",
            session_id="taskflow:default-public",
            active=False,
        )
        with pytest.raises(TaskFlowServiceError) as dormant_update_exc:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="running",
                actor_type="employee",
                actor_ref="default",
                actor_session_id="taskflow:default-public",
                session_id="taskflow:default-public",
            )
        assert dormant_update_exc.value.error_code == "task_actor_required"

        await _create_chat_session(
            factory,
            profile_id="default",
            session_id="taskflow:default-public",
        )
        claimed = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="running",
            actor_type="employee",
            actor_ref="default",
            actor_session_id="taskflow:default-public",
            session_id="taskflow:default-public",
        )
        assert claimed.status == "running"
        assert claimed.last_session_id == "taskflow:default-public"
        assert claimed.last_session_profile_id == "default"

        analyst_task = await service.create_task(
            profile_id="default",
            title="Analyst public mutation target",
            description="A different employee must not reuse another live task session.",
            created_by_type="employee",
            created_by_ref="default",
            actor_session_id="taskflow:default-public",
            owner_type="employee",
            owner_ref="analyst",
        )
        with pytest.raises(TaskFlowServiceError) as hijack_exc:
            await service.update_task(
                profile_id="default",
                task_id=analyst_task.id,
                status="running",
                actor_type="employee",
                actor_ref="analyst",
                actor_session_id="taskflow:default-public",
                session_id="taskflow:default-public",
            )
        assert hijack_exc.value.error_code == "task_actor_required"

        reviewed = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="review",
            actor_type="human",
            actor_ref="cli",
        )
        assert reviewed.status == "review"

        with pytest.raises(TaskFlowServiceError) as approve_exc:
            await service.approve_review_task(
                profile_id="default",
                task_id=task.id,
            )
        assert approve_exc.value.error_code == "task_review_actor_required"

        with pytest.raises(TaskFlowServiceError) as changes_exc:
            await service.request_review_changes(
                profile_id="default",
                task_id=task.id,
                reason_text="Anonymous review change request must be rejected.",
            )
        assert changes_exc.value.error_code == "task_review_actor_required"

        with pytest.raises(TaskFlowServiceError) as add_dep_exc:
            await service.add_dependency(
                profile_id="default",
                task_id=task.id,
                depends_on_task_id=dependency_root.id,
            )
        assert add_dep_exc.value.error_code == "task_actor_required"

        dependency = await service.add_dependency(
            profile_id="default",
            task_id=task.id,
            depends_on_task_id=dependency_root.id,
            actor_type="human",
            actor_ref="cli",
        )
        assert dependency.task_id == task.id

        with pytest.raises(TaskFlowServiceError) as remove_dep_exc:
            await service.remove_dependency(
                profile_id="default",
                task_id=task.id,
                depends_on_task_id=dependency_root.id,
            )
        assert remove_dep_exc.value.error_code == "task_actor_required"
    finally:
        await engine.dispose()


async def test_task_flow_service_reassignment_clears_stale_session_binding(tmp_path: Path) -> None:
    """Owner handoff should drop stale session bindings until the new owner binds a fresh session."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_reassign_session_reset.db",
        profile_ids=("default", "papercliper"),
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Reassign with session reset",
            description="Ensure stale session bindings do not survive owner handoff.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        running = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="running",
            session_id="taskflow:default-active",
        )
        assert running.last_session_id == "taskflow:default-active"
        assert running.last_session_profile_id == "default"

        reassigned = await service.update_task(
            profile_id="default",
            task_id=task.id,
            owner_type="employee",
            owner_ref="papercliper",
        )
        assert reassigned.owner_ref == "papercliper"
        assert reassigned.status == "todo"
        assert reassigned.last_session_id is None
        assert reassigned.last_session_profile_id is None

        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="running",
            )
        assert exc_info.value.error_code == "task_session_required"
    finally:
        await engine.dispose()


async def test_task_flow_service_preserves_block_reason_on_unrelated_updates(
    tmp_path: Path,
) -> None:
    """Updating metadata on blocked tasks should not silently clear the blocker semantics."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_block_reason_preserve.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Blocked task",
            description="Preserve blocker details across unrelated updates.",
            created_by_type="human",
            created_by_ref="cli",
        )
        blocked = await service.block_task(
            profile_id="default",
            task_id=task.id,
            reason_code="awaiting_human_review",
            reason_text="Waiting for operator review.",
            actor_type="human",
            actor_ref="cli",
        )
        assert blocked.blocked_reason_code == "awaiting_human_review"

        updated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            priority=10,
            labels=("ops",),
        )
        assert updated.status == "blocked"
        assert updated.blocked_reason_code == "awaiting_human_review"
        assert updated.blocked_reason_text == "Waiting for operator review."
    finally:
        await engine.dispose()


async def test_task_flow_service_keeps_live_session_activity_after_status_handoff(
    tmp_path: Path,
) -> None:
    """Live session indicators should survive mid-dialog status changes like review handoff."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_live_activity_handoff.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Mid-dialog review handoff",
            description="Keep showing session activity while the turn is still alive.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="running",
            session_id="taskflow:live-review",
        )

        touched_at = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            queue_repo = ChatSessionTurnQueueRepository(session)
            marker = await queue_repo.enqueue(
                profile_id="default",
                session_id="taskflow:live-review",
                owner_token="pytest:live-review",
                source="taskflow",
                client_msg_id="pytest-live-review",
            )
            started = await queue_repo.try_mark_running(
                queue_item_id=marker.id,
                profile_id="default",
                session_id="taskflow:live-review",
                owner_token="pytest:live-review",
                touched_at=touched_at,
            )
            assert started is True

        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="review",
        )
        refreshed = await service.get_task(profile_id="default", task_id=task.id)
        assert refreshed.active_session is not None
        assert refreshed.active_session.session_id == "taskflow:live-review"
        assert refreshed.active_session.running_turn_count == 1
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_dependency_wait_ready_at_conflict(tmp_path: Path) -> None:
    """dependency_wait blockers should never be combined with a timed revisit."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_dependency_wait_conflict.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Wait for teammate",
            description="Block until a delegated teammate task completes.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )

        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.update_task(
                profile_id="default",
                task_id=task.id,
                status="blocked",
                blocked_reason_code="dependency_wait",
                blocked_reason_text="Waiting for delegated task completion.",
                ready_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        assert exc_info.value.error_code == "task_dependency_wait_ready_at_conflict"
    finally:
        await engine.dispose()


async def test_task_flow_service_lists_task_runs_for_task_and_profile(tmp_path: Path) -> None:
    """Task run history should be queryable per task and across the profile backlog."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_task_runs.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Trace execution history",
            description="Collect execution history for operator review.",
            created_by_type="human",
            created_by_ref="cli",
        )
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            first_run = await repo.create_task_run(
                task_id=task.id,
                attempt=1,
                owner_type="employee",
                owner_ref="default",
                execution_mode="detached",
                status="completed",
                session_id="taskflow:history-1",
                run_id=11,
                worker_id="taskflow-runtime:0",
                started_at=task.created_at,
            )
            await repo.update_task_run(
                task_run_id=first_run.id,
                status="completed",
                summary="First pass completed.",
                finished_at=task.created_at,
            )
            second_run = await repo.create_task_run(
                task_id=task.id,
                attempt=2,
                owner_type="employee",
                owner_ref="default",
                execution_mode="manual",
                status="review",
                session_id="taskflow:history-2",
                run_id=12,
                worker_id=None,
                started_at=task.created_at,
            )
            await repo.update_task_run(
                task_run_id=second_run.id,
                status="review",
                summary="Waiting for review.",
                error_code="awaiting_review",
                error_text="Waiting for manual review.",
                finished_at=task.created_at,
            )

        task_runs = await service.list_task_runs(profile_id="default", task_id=task.id)
        assert [item.id for item in task_runs] == [second_run.id, first_run.id]
        assert task_runs[0].error_code == "awaiting_review"

        profile_runs = await service.list_task_runs(profile_id="default")
        assert [item.id for item in profile_runs] == [second_run.id, first_run.id]

        fetched_run = await service.get_task_run(profile_id="default", task_run_id=second_run.id)
        assert fetched_run.task_id == task.id
        assert fetched_run.summary == "Waiting for review."
    finally:
        await engine.dispose()


async def test_task_flow_service_reassignment_releases_running_claim(tmp_path: Path) -> None:
    """Owner reassignment should release any active claim before handing off work."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_reassign_running.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Running Employee task",
            description="Work on a detached background task.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )
        claim_now_utc = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed = await repo.claim_next_runnable_task(
                now_utc=claim_now_utc,
                lease_until=claim_now_utc + timedelta(minutes=15),
                claim_token="claim-reassign",
                claimed_by="taskflow-runtime:0",
            )
            assert claimed is not None
            assert claimed.id == task.id
            started = await repo.mark_task_started(
                task_id=task.id,
                claim_token="claim-reassign",
                started_at=claim_now_utc,
            )
            assert started is True

        updated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            owner_type="employee",
            owner_ref="reviewer",
        )

        assert updated.owner_type == "employee"
        assert updated.owner_ref == "reviewer"
        assert updated.status == "todo"

        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            refreshed = await repo.get_task(profile_id="default", task_id=task.id)
            assert refreshed is not None
            assert refreshed.claim_token is None
            assert refreshed.claimed_by is None
            assert refreshed.lease_until is None
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_second_manual_active_task_for_ai_owner(
    tmp_path: Path,
) -> None:
    """Manual status updates should preserve the one-active-task-per-agent invariant."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_manual_active_limit.db",
        profile_ids=("default", "analyst"),
    )
    service = TaskFlowService(factory)
    try:
        first = await service.create_task(
            profile_id="default",
            title="Analyst active work",
            description="Hold the only active slot for analyst.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
        )
        second = await service.create_task(
            profile_id="default",
            title="Analyst queued work",
            description="This should stay queued until analyst is free.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
        )

        active = await service.update_task(
            profile_id="default",
            task_id=first.id,
            status="running",
            session_id="taskflow:first",
        )
        assert active.status == "running"

        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.update_task(
                profile_id="default",
                task_id=second.id,
                status="running",
                session_id="taskflow:second",
            )

        assert exc_info.value.error_code == "task_owner_active_conflict"
    finally:
        await engine.dispose()


async def test_task_flow_service_allows_same_ai_owner_ref_in_other_profile(
    tmp_path: Path,
) -> None:
    """Manual active-task guard should be scoped per profile for Employee owners."""

    db_name = "task_flow_manual_active_limit_cross_profile.db"
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    _write_team_runtime_config(
        settings=settings,
        profile_id="default",
        team_profile_ids=("analyst",),
    )
    _write_team_runtime_config(
        settings=settings,
        profile_id="researcher",
        team_profile_ids=("analyst",),
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "researcher", "analyst"),
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        default_task = await service.create_task(
            profile_id="default",
            title="Default analyst active work",
            description="Keep analyst occupied in default profile.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
        )
        researcher_task = await service.create_task(
            profile_id="researcher",
            title="Researcher analyst active work",
            description="Same owner ref, isolated by profile.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="analyst",
        )

        default_active = await service.update_task(
            profile_id="default",
            task_id=default_task.id,
            status="running",
            session_id="session-default-analyst",
            session_profile_id="default",
            actor_type="employee",
            actor_ref="analyst",
            actor_session_id="session-default-analyst",
        )
        assert default_active.status == "running"

        researcher_active = await service.update_task(
            profile_id="researcher",
            task_id=researcher_task.id,
            status="running",
            session_id="session-researcher-analyst",
            session_profile_id="researcher",
            actor_type="employee",
            actor_ref="analyst",
            actor_session_id="session-researcher-analyst",
        )
        assert researcher_active.status == "running"
    finally:
        await engine.dispose()


async def test_task_flow_service_records_append_only_task_events(tmp_path: Path) -> None:
    """Task events should capture user-visible state transitions and dependency reconciliation."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_events.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Prepare launch brief",
            description="Draft the launch brief for operator review.",
            created_by_type="human",
            created_by_ref="cli",
        )
        updated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="review",
            priority=90,
            labels=("launch", "ops"),
            actor_type="human",
            actor_ref="cli_user:alice",
        )
        assert updated.status == "review"

        events = await service.list_task_events(profile_id="default", task_id=task.id)
        assert [item.event_type for item in events] == ["updated", "wake_requested", "created"]
        updated_event = events[0]
        assert updated_event.actor_type == "human"
        assert updated_event.actor_ref == "cli_user:alice"
        assert updated_event.from_status == "todo"
        assert updated_event.to_status == "review"
        assert updated_event.details["priority"] == {"before": 50, "after": 90}
        assert updated_event.details["status"] == {"before": "todo", "after": "review"}
        assert updated_event.details["labels"] == {"before": [], "after": ["launch", "ops"]}

        prereq = await service.create_task(
            profile_id="default",
            title="Collect metrics",
            description="Collect the input metrics first.",
            created_by_type="human",
            created_by_ref="cli",
        )
        dependent = await service.create_task(
            profile_id="default",
            title="Publish metrics memo",
            description="Publish the memo after metrics are collected.",
            created_by_type="human",
            created_by_ref="cli",
        )
        await service.add_dependency(
            profile_id="default",
            task_id=dependent.id,
            depends_on_task_id=prereq.id,
        )
        dependency_events = await service.list_task_events(
            profile_id="default", task_id=dependent.id
        )
        assert [item.event_type for item in dependency_events[:4]] == [
            "dependencies_blocked",
            "dependency_added",
            "wake_requested",
            "created",
        ]

        await service.remove_dependency(
            profile_id="default",
            task_id=dependent.id,
            depends_on_task_id=prereq.id,
        )
        dependency_events = await service.list_task_events(
            profile_id="default", task_id=dependent.id
        )
        assert [item.event_type for item in dependency_events[:7]] == [
            "wake_requested",
            "dependencies_satisfied",
            "dependency_removed",
            "dependencies_blocked",
            "dependency_added",
            "wake_requested",
            "created",
        ]
    finally:
        await engine.dispose()


async def test_task_flow_service_adds_and_lists_comments_and_surfaces_them_in_inbox(
    tmp_path: Path,
) -> None:
    """Task comments should be append-only and surface in the employee feed."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_comments.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Review final answer",
            description="Review the drafted answer and leave precise notes.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="employee",
            owner_ref="default",
        )

        comment = await service.add_task_comment(
            profile_id="default",
            task_id=task.id,
            message="Please add citations before sending.",
            actor_type="human",
            actor_ref="cli",
            comment_type="review_feedback",
        )
        assert comment.task_id == task.id
        assert comment.comment_type == "review_feedback"
        assert comment.message == "Please add citations before sending."

        comments = await service.list_task_comments(profile_id="default", task_id=task.id)
        assert len(comments) == 1
        assert comments[0].id == comment.id
        assert comments[0].actor_ref == "cli"

        events = await service.list_task_events(profile_id="default", task_id=task.id)
        comment_event = next(event for event in events if event.event_type == "comment_added")
        assert comment_event.details["comment_type"] == "review_feedback"

        feed = await service.build_employee_inbox(
            profile_id="default",
            owner_type="employee",
            owner_ref="default",
        )
        assert any(
            event.event_type == "wake_requested"
            and event.details.get("reason_code") == "comment_added"
            for event in feed.recent_events
        )

        await service.add_task_comment(
            profile_id="default",
            task_id=task.id,
            message="Human reviewer note.",
            actor_type="human",
            actor_ref="cli_user:alice",
            comment_type="note",
        )
        fresh_feed = await service.build_employee_inbox(
            profile_id="default",
            owner_type="employee",
            owner_ref="default",
        )
        assert fresh_feed.recent_events[0].event_type == "wake_requested"
        assert fresh_feed.recent_events[0].details["reason_code"] == "comment_added"
        assert fresh_feed.recent_events[0].message == "Comment added for the responsible employee."
    finally:
        await engine.dispose()


async def test_task_flow_service_uses_description_plan_and_task_attachments(
    tmp_path: Path,
) -> None:
    """Tasks should expose description, support PLAN state, and persist attachments."""

    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name="task_flow_description_plan_attachments.db",
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_description_plan_attachments.db",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Prepare implementation draft",
            description="Collect requirements, attach source files, and keep the task in plan.",
            status="plan",
            owner_type="employee",
            owner_ref="default",
            created_by_type="human",
            created_by_ref="cli",
            attachments=(
                {
                    "name": "requirements.txt",
                    "content_type": "text/plain",
                    "content_base64": "bWlncmF0ZSB0byBkZXNjcmlwdGlvbg==",
                },
            ),
        )

        assert (
            task.description
            == "Collect requirements, attach source files, and keep the task in plan."
        )
        assert task.status == "plan"
        assert task.attachment_count == 1

        board = await service.build_board(profile_id="default")
        assert board.columns[0].id == "plan"
        assert board.columns[0].tasks[0].id == task.id
        assert board.ready_count == 0

        attachments = await service.list_task_attachments(profile_id="default", task_id=task.id)
        assert len(attachments) == 1
        assert attachments[0].name == "requirements.txt"
        assert attachments[0].content_type == "text/plain"
        assert attachments[0].byte_size == 22

        content = await service.get_task_attachment_content(
            profile_id="default",
            task_id=task.id,
            attachment_id=attachments[0].id,
        )
        assert content.content_bytes == b"migrate to description"
    finally:
        await engine.dispose()


async def test_task_flow_service_allows_employee_owned_plan_task_creation(tmp_path: Path) -> None:
    """Employee-owned PLAN tasks are planning backlog items and are not runtime runnable."""

    db_name = "task_flow_plan_employee_owner_create.db"
    engine, factory = await build_repository_factory(
        tmp_path, db_name=db_name, profile_ids=("default",)
    )
    service = TaskFlowService(factory)

    try:
        task = await service.create_task(
            profile_id="default",
            title="CTO planning ticket",
            description="Keep this in PLAN while the responsible employee decomposes the work.",
            status="plan",
            owner_type="employee",
            owner_ref="default",
            created_by_type="human",
            created_by_ref="cli",
        )

        assert task.status == "plan"
        assert task.owner_type == "employee"
        assert task.owner_ref == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_allows_employee_task_transition_to_plan(tmp_path: Path) -> None:
    """Managers can move employee-owned work back to PLAN for decomposition."""

    db_name = "task_flow_plan_employee_owner_update.db"
    engine, factory = await build_repository_factory(
        tmp_path, db_name=db_name, profile_ids=("default",)
    )
    service = TaskFlowService(factory)

    try:
        task = await service.create_task(
            profile_id="default",
            title="Runtime ticket",
            description="Initially runnable.",
            status="todo",
            owner_type="employee",
            owner_ref="default",
            created_by_type="human",
            created_by_ref="cli",
        )

        updated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="plan",
            actor_type="employee",
            actor_ref="default",
        )

        assert updated.status == "plan"
        assert updated.owner_type == "employee"
        assert updated.owner_ref == "default"
    finally:
        await engine.dispose()


async def test_task_flow_service_allows_employee_owner_swap_on_existing_plan_task(
    tmp_path: Path,
) -> None:
    """PLAN tasks can be reassigned between employees without falling back to human owners."""

    db_name = "task_flow_plan_employee_owner_reassign.db"
    engine, factory = await build_repository_factory(
        tmp_path, db_name=db_name, profile_ids=("default",)
    )
    service = TaskFlowService(factory)

    try:
        task = await service.create_task(
            profile_id="default",
            title="Employee triage",
            description="Employee-owned plan task.",
            status="plan",
            owner_type="employee",
            owner_ref="default",
            created_by_type="human",
            created_by_ref="cli",
        )

        updated = await service.update_task(
            profile_id="default",
            task_id=task.id,
            owner_type="employee",
            owner_ref="analyst",
            actor_type="human",
            actor_ref="cli",
        )

        assert updated.status == "plan"
        assert updated.owner_type == "employee"
        assert updated.owner_ref == "analyst"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_oversized_base64_attachment_before_decode(
    tmp_path: Path,
) -> None:
    """Attachment payloads should be rejected on encoded size before base64 decode runs."""

    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name="task_flow_attachment_base64_limit.db",
    )
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_attachment_base64_limit.db",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        with pytest.raises(TaskFlowServiceError, match="maximum encoded size"):
            await service.create_task(
                profile_id="default",
                title="Reject huge attachment",
                description="Fail before decode when attachment base64 is oversized.",
                created_by_type="human",
                created_by_ref="cli",
                attachments=(
                    {
                        "name": "oversized.bin",
                        "content_base64": "A" * (_MAX_TASK_ATTACHMENT_BASE64_BYTES + 1),
                    },
                ),
            )
    finally:
        await engine.dispose()
