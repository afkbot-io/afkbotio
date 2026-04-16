"""Tests for the Task Flow service layer."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from afkbot.db.session import session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.chat_session_turn_queue_repo import ChatSessionTurnQueueRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.profile_runtime import ProfileRuntimeConfig, get_profile_runtime_config_service
from afkbot.services.task_flow import TaskFlowServiceError
from afkbot.services.task_flow.service import TaskFlowService
from afkbot.settings import Settings
from tests.repositories._harness import build_repository_factory


def _taskflow_test_settings(
    *,
    tmp_path: Path,
    db_name: str,
    taskflow_public_principal_required: bool = False,
    taskflow_strict_team_profile_ids: bool = False,
) -> Settings:
    return Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}",
        root_dir=tmp_path,
        chat_human_owner_ref="cli",
        taskflow_public_principal_required=taskflow_public_principal_required,
        taskflow_strict_team_profile_ids=taskflow_strict_team_profile_ids,
    )


def _write_team_runtime_config(
    *,
    settings: Settings,
    profile_id: str,
    team_profile_ids: tuple[str, ...],
) -> None:
    get_profile_runtime_config_service(settings).write(
        profile_id,
        ProfileRuntimeConfig(
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            taskflow_team_profile_ids=team_profile_ids,
        ),
    )


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
            default_owner_type="human",
            default_owner_ref="cli_user:alice",
            labels=("launch",),
        )
        assert flow.default_owner_type == "human"
        assert flow.default_owner_ref == "cli_user:alice"

        first_task = await service.create_task(
            profile_id="default",
            flow_id=flow.id,
            title="Draft release notes",
            description="Prepare release notes from the current changelog.",
            created_by_type="human",
            created_by_ref="cli",
        )
        assert first_task.flow_id == flow.id
        assert first_task.owner_type == "human"
        assert first_task.owner_ref == "cli_user:alice"
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
        assert dependent_task.owner_ref == "cli_user:alice"
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
            owner_type="human",
            owner_ref="cli_user:alice",
        )
        assert {item.id for item in listed} == {first_task.id, dependent_task.id}
        listed_by_id = {item.id: item for item in listed}
        assert listed_by_id[dependent_task.id].depends_on_task_ids == (first_task.id,)
    finally:
        await engine.dispose()


async def test_task_flow_service_summarizes_human_owned_tasks(tmp_path: Path) -> None:
    """Startup summaries should count visible human task states and cap the preview list."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_summary.db",
    )
    service = TaskFlowService(factory)
    try:
        review_task = await service.create_task(
            profile_id="default",
            title="Review landing page",
            description="Review final copy and approve publishing.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            due_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        await service.update_task(
            profile_id="default",
            task_id=review_task.id,
            status="review",
        )

        blocked_task = await service.create_task(
            profile_id="default",
            title="Wait for assets",
            description="Collect final media files.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            depends_on_task_ids=(review_task.id,),
        )
        todo_task = await service.create_task(
            profile_id="default",
            title="Send newsletter",
            description="Send the launch newsletter.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
        )

        summary = await service.summarize_human_tasks(
            profile_id="default",
            owner_ref="cli_user:alice",
            limit=2,
        )

        assert summary.total_count == 3
        assert summary.todo_count == 1
        assert summary.blocked_count == 1
        assert summary.review_count == 1
        assert summary.overdue_count == 1
        assert len(summary.tasks) == 2
        assert {item.id for item in summary.tasks} <= {
            review_task.id,
            blocked_task.id,
            todo_task.id,
        }
    finally:
        await engine.dispose()


async def test_task_flow_service_summarizes_human_review_inbox_items(tmp_path: Path) -> None:
    """Human startup summaries should include review tasks routed via reviewer fields."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_review_inbox.db",
    )
    service = TaskFlowService(factory)
    try:
        review_task = await service.create_task(
            profile_id="default",
            title="Review AI analysis",
            description="Review the AI draft and approve or request changes.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="human",
            reviewer_ref="cli_user:alice",
        )
        await service.update_task(
            profile_id="default",
            task_id=review_task.id,
            status="review",
        )
        todo_task = await service.create_task(
            profile_id="default",
            title="Follow up with user",
            description="Send the final follow-up after review.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
        )

        summary = await service.summarize_human_tasks(
            profile_id="default",
            owner_ref="cli_user:alice",
            limit=5,
        )

        assert summary.total_count == 2
        assert summary.todo_count == 1
        assert summary.blocked_count == 0
        assert summary.review_count == 1
        assert summary.overdue_count == 0
        assert {item.id for item in summary.tasks} == {review_task.id, todo_task.id}
    finally:
        await engine.dispose()


async def test_task_flow_service_builds_notification_ready_human_inbox(tmp_path: Path) -> None:
    """Human inbox should expose counts, recent events, and channel-based dedupe cursors."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_human_inbox.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Review AI draft",
            description="Review the AI-produced answer.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
        )

        inbox = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            task_limit=5,
            event_limit=5,
        )
        assert inbox.total_count == 1
        assert inbox.todo_count == 1
        assert inbox.unseen_event_count == 1
        assert inbox.recent_events[0].task_id == task.id
        assert inbox.recent_events[0].event_type == "created"

        primed = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            task_limit=5,
            event_limit=5,
            channel="chat",
            mark_seen=True,
        )
        assert primed.channel == "chat"
        assert primed.unseen_event_count == 0
        assert primed.recent_events == ()

        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="blocked",
            blocked_reason_code="awaiting_input",
            blocked_reason_text="Waiting for operator input.",
            actor_type="human",
            actor_ref="cli_user:bob",
        )

        notified = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            task_limit=5,
            event_limit=5,
            channel="chat",
        )
        assert notified.unseen_event_count == 1
        assert notified.recent_events[0].event_type == "updated"
        assert notified.recent_events[0].actor_ref == "cli_user:bob"
        assert notified.recent_events[0].to_status == "blocked"
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
            title="Recover stale AI task",
            description="Recover the stale AI task after lease expiry.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
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
            title="Review AI draft",
            description="Review the AI draft response.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="human",
            reviewer_ref="cli_user:alice",
            labels=("review",),
        )
        await service.update_task(profile_id="default", task_id=explicit_reviewer.id, status="review")

        owner_fallback = await service.create_task(
            profile_id="default",
            title="Owner fallback review",
            description="Review directly as the human owner.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            labels=("review",),
        )
        await service.update_task(profile_id="default", task_id=owner_fallback.id, status="review")

        other_reviewer = await service.create_task(
            profile_id="default",
            title="Other reviewer task",
            description="Should not appear in Alice inbox.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="human",
            reviewer_ref="cli_user:bob",
            labels=("review",),
        )
        await service.update_task(profile_id="default", task_id=other_reviewer.id, status="review")

        inbox = await service.list_review_tasks(
            profile_id="default",
            actor_type="human",
            actor_ref="cli_user:alice",
            labels=("review",),
        )

        assert {item.id for item in inbox} == {explicit_reviewer.id, owner_fallback.id}
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
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="human",
            reviewer_ref="cli_user:alice",
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
                actor_type="human",
                actor_ref="cli_user:bob",
            )

        approved = await service.approve_review_task(
            profile_id="default",
            task_id=review_task.id,
            actor_type="human",
            actor_ref="cli_user:alice",
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
            owner_type="human",
            owner_ref="cli_user:alice",
        )
        await service.update_task(profile_id="default", task_id=second_review.id, status="review")

        changed = await service.request_review_changes(
            profile_id="default",
            task_id=second_review.id,
            actor_type="human",
            actor_ref="cli_user:alice",
            owner_type="ai_profile",
            owner_ref="default",
            reason_text="Needs source citations before approval.",
        )
        assert changed.status == "blocked"
        assert changed.owner_type == "ai_profile"
        assert changed.owner_ref == "default"
        assert changed.blocked_reason_code == "review_changes_requested"
        assert changed.blocked_reason_text == "Needs source citations before approval."
    finally:
        await engine.dispose()


async def test_task_flow_service_request_review_changes_respects_team_roster(
    tmp_path: Path,
) -> None:
    """AI reviewers should not be able to bounce reviewed work to arbitrary AI profiles."""

    db_name = "task_flow_review_assignment_guard.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "papercliper", "outsider"),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_strict_team_profile_ids=True,
    )
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
            title="AI reviewed task",
            description="Review and send back if changes are needed.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            reviewer_type="ai_profile",
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
                actor_type="ai_profile",
                actor_ref="papercliper",
                actor_session_id="taskflow:papercliper-review",
                owner_type="ai_profile",
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
            owner_type="human",
            owner_ref="cli_user:alice",
        )
        todo_task = await service.create_task(
            profile_id="default",
            title="Todo overdue",
            description="Finish the overdue item.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
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
            owner_type="human",
            owner_ref="cli_user:alice",
            depends_on_task_ids=(prereq.id,),
            labels=("release",),
        )
        review_task = await service.create_task(
            profile_id="default",
            title="Needs human review",
            description="Wait for review before completion.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="human",
            reviewer_ref="cli_user:alice",
            labels=("release",),
        )
        await service.update_task(profile_id="default", task_id=review_task.id, status="review")
        completed_task = await service.create_task(
            profile_id="default",
            title="Completed item",
            description="Already completed.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            labels=("release",),
        )
        await service.update_task(profile_id="default", task_id=completed_task.id, status="completed")
        failed_task = await service.create_task(
            profile_id="default",
            title="Failed item",
            description="Already failed.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            labels=("release",),
        )
        await service.update_task(profile_id="default", task_id=failed_task.id, status="failed")
        cancelled_task = await service.create_task(
            profile_id="default",
            title="Cancelled item",
            description="Already cancelled.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            labels=("release",),
        )
        await service.update_task(profile_id="default", task_id=cancelled_task.id, status="cancelled")
        await service.create_task(
            profile_id="default",
            title="Claimed item",
            description="Claimed by background worker.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            priority=60,
            labels=("release",),
        )
        running_task = await service.create_task(
            profile_id="default",
            title="Running item",
            description="Running in background.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
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
                claim_token="claim-board-1" if running_row.id == claimed_row.id else "claim-board-2",
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
            owner_type="ai_profile",
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
            owner_type="ai_profile",
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
            owner_type="ai_profile",
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


async def test_task_flow_service_delegate_task_creates_handoff_and_dependency(
    tmp_path: Path,
) -> None:
    """Delegation should create a child AI task and block the source task on it when requested."""

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
            owner_type="ai_profile",
            owner_ref="analyst",
            flow_id=None,
            labels=("launch", "brief"),
        )

        delegation = await service.delegate_task(
            profile_id="default",
            source_task_id=source_task.id,
            delegated_owner_ref="papercliper",
            description="Research competitor messaging and deliver a concise summary.",
            actor_type="ai_profile",
            actor_ref="analyst",
            actor_session_id="taskflow:analyst-delegate",
            wait_for_delegated_task=True,
        )

        assert delegation.source_task.id == source_task.id
        assert delegation.source_task.status == "blocked"
        assert delegation.source_task.blocked_reason_code == "dependency_wait"
        assert delegation.delegated_task.owner_type == "ai_profile"
        assert delegation.delegated_task.owner_ref == "papercliper"
        assert delegation.delegated_task.source_type == "task_delegation"
        assert delegation.delegated_task.source_ref == source_task.id
        assert delegation.delegated_task.created_by_type == "ai_profile"
        assert delegation.delegated_task.created_by_ref == "analyst"
        assert delegation.dependency is not None
        assert delegation.dependency.task_id == source_task.id
        assert delegation.dependency.depends_on_task_id == delegation.delegated_task.id

        comments = await service.list_task_comments(profile_id="default", task_id=source_task.id)
        assert comments[0].comment_type == "delegation"
        assert delegation.delegated_task.id in comments[0].message
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
            owner_type="ai_profile",
            owner_ref="default",
        )
        scheduled_ready_at = datetime.now(timezone.utc) + timedelta(hours=2)
        scheduled_task = await service.block_task(
            profile_id="default",
            task_id=scheduled_task.id,
            reason_code="vendor_pending",
            reason_text="The external vendor has not replied yet.",
            actor_type="ai_profile",
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
            owner_type="ai_profile",
            owner_ref="default",
        )
        human_wait_task = await service.block_task(
            profile_id="default",
            task_id=human_wait_task.id,
            reason_code="awaiting_human_input",
            reason_text="Need approval from Alice.",
            actor_type="ai_profile",
            actor_ref="default",
            owner_type="human",
            owner_ref="cli_user:alice",
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
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="human",
            reviewer_ref="cli_user:alice",
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
            title="Prepare AI review",
            description="Route the draft to another AI reviewer.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="ai_profile",
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
            owner_type="ai_profile",
            owner_ref="default",
        )
        dependent_task = await service.create_task(
            profile_id="default",
            title="Wait on prerequisite",
            description="Continue only after the prerequisite is done.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
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
            actor_type="ai_profile",
            actor_ref="default",
        )
        assert vendor_blocked_task.block_state is not None
        assert vendor_blocked_task.block_state.kind == "blocked"
        assert vendor_blocked_task.block_state.waiting_for_dependency is False
        assert vendor_blocked_task.block_state.depends_on_task_ids == (prerequisite_task.id,)
    finally:
        await engine.dispose()


async def test_task_flow_service_legacy_backlog_allows_cross_profile_ai_without_team_config(
    tmp_path: Path,
) -> None:
    """Legacy mode should preserve cross-profile AI assignment when team roster is not configured."""

    db_name = "task_flow_legacy_backlog_team_unset.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst", "papercliper"),
    )
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    service = TaskFlowService(factory, settings=settings)
    try:
        created = await service.create_task(
            profile_id="default",
            title="Cross-profile analyst task",
            description="Allow analyst profile to own this task in legacy mode.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )
        assert created.owner_type == "ai_profile"
        assert created.owner_ref == "analyst"

        reassigned = await service.update_task(
            profile_id="default",
            task_id=created.id,
            owner_type="ai_profile",
            owner_ref="papercliper",
            actor_type="human",
            actor_ref="cli",
        )
        assert reassigned.owner_type == "ai_profile"
        assert reassigned.owner_ref == "papercliper"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_non_team_ai_assignment(tmp_path: Path) -> None:
    """Strict roster mode should reject cross-profile AI assignment without team config."""

    db_name = "task_flow_team_permissions.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst", "papercliper"),
    )
    settings = _taskflow_test_settings(tmp_path=tmp_path, db_name=db_name)
    await _create_chat_session(
        factory,
        profile_id="analyst",
        session_id="taskflow:analyst-create",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        created = await service.create_task(
            profile_id="default",
            title="Cross-profile assignment",
            description="Try to assign work to a teammate without team membership.",
            created_by_type="ai_profile",
            created_by_ref="analyst",
            actor_session_id="taskflow:analyst-create",
            owner_type="ai_profile",
            owner_ref="papercliper",
        )
        assert created.owner_type == "ai_profile"
        assert created.owner_ref == "papercliper"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_ai_creator_outside_backlog_roster(tmp_path: Path) -> None:
    """AI actors outside the configured roster should not admit work into another backlog."""

    db_name = "task_flow_creator_permissions.db"
    engine, factory = await build_repository_factory(
        tmp_path,
        db_name=db_name,
        profile_ids=("default", "analyst"),
    )
    settings = _taskflow_test_settings(
        tmp_path=tmp_path,
        db_name=db_name,
        taskflow_strict_team_profile_ids=True,
    )
    await _create_chat_session(
        factory,
        profile_id="analyst",
        session_id="taskflow:analyst-create",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.create_task(
                profile_id="default",
                title="Injected backlog task",
                description="Try to inject work into another backlog's manager queue.",
                created_by_type="ai_profile",
                created_by_ref="analyst",
                actor_session_id="taskflow:analyst-create",
                owner_type="ai_profile",
                owner_ref="default",
            )

        assert exc_info.value.error_code == "task_creator_forbidden"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_manager_assignment_outside_team_roster(tmp_path: Path) -> None:
    """Backlog manager AI should still be constrained to its configured teammate roster."""

    db_name = "task_flow_manager_assignment.db"
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
        profile_id="default",
        session_id="taskflow:default-manager",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.create_task(
                profile_id="default",
                title="Manager assigns outsider",
                description="Attempt to assign work to a profile outside the roster.",
                created_by_type="ai_profile",
                created_by_ref="default",
                actor_session_id="taskflow:default-manager",
                owner_type="ai_profile",
                owner_ref="outsider",
            )

        assert exc_info.value.error_code == "task_owner_forbidden"
    finally:
        await engine.dispose()


async def test_task_flow_service_rejects_ai_actor_mutating_coworker_task(tmp_path: Path) -> None:
    """AI workers should only mutate their own tasks unless they are the backlog manager."""

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
        profile_id="analyst",
        session_id="taskflow:analyst-demo",
    )
    service = TaskFlowService(factory, settings=settings)
    try:
        coworker_task = await service.create_task(
            profile_id="default",
            title="Papercliper owned task",
            description="Own this task as Papercliper.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="papercliper",
        )

        with pytest.raises(TaskFlowServiceError) as exc_info:
            await service.update_task(
                profile_id="default",
                task_id=coworker_task.id,
                status="running",
                actor_type="ai_profile",
                actor_ref="analyst",
                actor_session_id="taskflow:analyst-demo",
                session_id="taskflow:analyst-demo",
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
            owner_type="human",
            owner_ref="cli",
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


async def test_task_flow_service_requires_actor_identity_on_public_mutations(tmp_path: Path) -> None:
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
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="ai_profile",
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
                actor_type="ai_profile",
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
                actor_type="ai_profile",
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
            actor_type="ai_profile",
            actor_ref="default",
            actor_session_id="taskflow:default-public",
            session_id="taskflow:default-public",
        )
        assert claimed.status == "running"
        assert claimed.last_session_id == "taskflow:default-public"
        assert claimed.last_session_profile_id == "default"

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
            owner_type="ai_profile",
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
            owner_type="ai_profile",
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


async def test_task_flow_service_preserves_block_reason_on_unrelated_updates(tmp_path: Path) -> None:
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
            owner_type="ai_profile",
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
            owner_type="ai_profile",
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
                owner_type="ai_profile",
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
                owner_type="human",
                owner_ref="cli_user:alice",
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
            title="Running AI task",
            description="Work on a detached background task.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
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
            owner_type="human",
            owner_ref="cli_user:alice",
        )

        assert updated.owner_type == "human"
        assert updated.owner_ref == "cli_user:alice"
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
            owner_type="ai_profile",
            owner_ref="analyst",
        )
        second = await service.create_task(
            profile_id="default",
            title="Analyst queued work",
            description="This should stay queued until analyst is free.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
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
    """Manual active-task guard should be scoped per profile for AI owners."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_manual_active_limit_cross_profile.db",
        profile_ids=("default", "researcher", "analyst"),
    )
    service = TaskFlowService(factory)
    try:
        default_task = await service.create_task(
            profile_id="default",
            title="Default analyst active work",
            description="Keep analyst occupied in default profile.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )
        researcher_task = await service.create_task(
            profile_id="researcher",
            title="Researcher analyst active work",
            description="Same owner ref, isolated by profile.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="analyst",
        )

        default_active = await service.update_task(
            profile_id="default",
            task_id=default_task.id,
            status="running",
            session_id="session-default-analyst",
            session_profile_id="analyst",
            actor_type="ai_profile",
            actor_ref="analyst",
            actor_session_id="session-default-analyst",
        )
        assert default_active.status == "running"

        researcher_active = await service.update_task(
            profile_id="researcher",
            task_id=researcher_task.id,
            status="running",
            session_id="session-researcher-analyst",
            session_profile_id="analyst",
            actor_type="ai_profile",
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
        assert [item.event_type for item in events] == ["updated", "created"]
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
        dependency_events = await service.list_task_events(profile_id="default", task_id=dependent.id)
        assert [item.event_type for item in dependency_events[:3]] == [
            "dependencies_blocked",
            "dependency_added",
            "created",
        ]

        await service.remove_dependency(
            profile_id="default",
            task_id=dependent.id,
            depends_on_task_id=prereq.id,
        )
        dependency_events = await service.list_task_events(profile_id="default", task_id=dependent.id)
        assert [item.event_type for item in dependency_events[:5]] == [
            "dependencies_satisfied",
            "dependency_removed",
            "dependencies_blocked",
            "dependency_added",
            "created",
        ]
    finally:
        await engine.dispose()


async def test_task_flow_service_builds_human_inbox_and_dedupes_channel_notifications(
    tmp_path: Path,
) -> None:
    """Human inbox should expose recent activity and dedupe startup-channel notifications."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_human_inbox.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Check release copy",
            description="Review the release copy before publishing.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
        )

        inbox = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
        )
        assert inbox.total_count == 1
        assert inbox.todo_count == 1
        assert inbox.unseen_event_count == 1
        assert inbox.recent_events[0].event_type == "created"
        assert inbox.recent_events[0].task_title == "Check release copy"

        first_startup = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            mark_seen=True,
        )
        assert first_startup.unseen_event_count == 0
        assert first_startup.recent_events == ()

        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="review",
            actor_type="human",
            actor_ref="cli_user:alice",
        )

        second_startup = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            mark_seen=True,
        )
        assert second_startup.unseen_event_count == 1
        assert second_startup.recent_events[0].event_type == "updated"
        assert second_startup.recent_events[0].to_status == "review"

        third_startup = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            mark_seen=True,
        )
        assert third_startup.unseen_event_count == 0
        assert third_startup.recent_events == ()
    finally:
        await engine.dispose()


async def test_task_flow_service_human_inbox_counts_all_relevant_events_beyond_preview_window(
    tmp_path: Path,
) -> None:
    """Human inbox should not lose older relevant events behind newer irrelevant noise."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_human_inbox_noise.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Review noisy task",
            description="Review the task after the runtime finishes unrelated work.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
        )
        await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            mark_seen=True,
        )
        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="review",
            actor_type="human",
            actor_ref="cli_user:alice",
        )
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            for index in range(24):
                await repo.create_task_event(
                    task_id=task.id,
                    event_type="execution_completed",
                    actor_type="runtime",
                    actor_ref="taskflow-runtime:0",
                    message=f"Irrelevant noise {index}",
                )

        inbox = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            event_limit=5,
            mark_seen=False,
        )
        assert inbox.unseen_event_count == 1
        assert len(inbox.recent_events) == 1
        assert inbox.recent_events[0].event_type == "updated"
        assert inbox.recent_events[0].to_status == "review"
    finally:
        await engine.dispose()


async def test_task_flow_service_human_inbox_avoids_materializing_full_unseen_event_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human inbox should use filtered count/preview queries instead of bulk event fetches."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_human_inbox_no_bulk_fetch.db",
    )
    service = TaskFlowService(factory)
    try:
        task = await service.create_task(
            profile_id="default",
            title="Review optimized inbox",
            description="Make sure inbox queries stay bounded.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
        )
        await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            mark_seen=True,
        )
        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="review",
            actor_type="human",
            actor_ref="cli_user:alice",
        )

        async def _unexpected_bulk_fetch(*args, **kwargs):
            raise AssertionError("build_human_inbox should not materialize the full unseen event tail")

        monkeypatch.setattr(TaskFlowRepository, "list_task_events_for_tasks", _unexpected_bulk_fetch)

        inbox = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            event_limit=1,
            mark_seen=False,
        )

        assert inbox.unseen_event_count == 1
        assert len(inbox.recent_events) == 1
        assert inbox.recent_events[0].event_type == "updated"
        assert inbox.recent_events[0].to_status == "review"
    finally:
        await engine.dispose()


async def test_task_flow_service_human_inbox_mark_seen_is_concurrency_safe(tmp_path: Path) -> None:
    """Concurrent inbox mark-seen calls should not race the notification cursor insert."""

    engine, factory = await build_repository_factory(
        tmp_path,
        db_name="task_flow_human_inbox_concurrency.db",
    )
    service = TaskFlowService(factory)
    try:
        await service.create_task(
            profile_id="default",
            title="Concurrent inbox task",
            description="Make sure concurrent inbox reads do not explode.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
        )

        inboxes = await asyncio.gather(
            *(
                service.build_human_inbox(
                    profile_id="default",
                    owner_ref="cli_user:alice",
                    channel="chat_startup",
                    mark_seen=True,
                )
                for _ in range(2)
            )
        )
        assert all(inbox.channel == "chat_startup" for inbox in inboxes)
        async with session_scope(factory) as session:
            cursor = await TaskFlowRepository(session).get_task_notification_cursor(
                profile_id="default",
                actor_type="human",
                actor_ref="cli_user:alice",
                channel="chat_startup",
            )
            assert cursor is not None
    finally:
        await engine.dispose()


async def test_task_flow_service_adds_and_lists_comments_and_surfaces_them_in_inbox(
    tmp_path: Path,
) -> None:
    """Task comments should be append-only and optionally appear as inbox activity."""

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
            owner_type="human",
            owner_ref="cli_user:alice",
        )

        comment = await service.add_task_comment(
            profile_id="default",
            task_id=task.id,
            message="Please add citations before sending.",
            actor_type="ai_profile",
            actor_ref="default",
            comment_type="review_feedback",
        )
        assert comment.task_id == task.id
        assert comment.comment_type == "review_feedback"
        assert comment.message == "Please add citations before sending."

        comments = await service.list_task_comments(profile_id="default", task_id=task.id)
        assert len(comments) == 1
        assert comments[0].id == comment.id
        assert comments[0].actor_ref == "default"

        events = await service.list_task_events(profile_id="default", task_id=task.id)
        assert events[0].event_type == "comment_added"
        assert events[0].details["comment_type"] == "review_feedback"

        startup_inbox = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            mark_seen=True,
        )
        assert startup_inbox.unseen_event_count == 0

        await service.add_task_comment(
            profile_id="default",
            task_id=task.id,
            message="Human reviewer note.",
            actor_type="human",
            actor_ref="cli_user:alice",
            comment_type="note",
        )
        fresh_inbox = await service.build_human_inbox(
            profile_id="default",
            owner_ref="cli_user:alice",
            channel="chat_startup",
            mark_seen=False,
        )
        assert fresh_inbox.unseen_event_count == 1
        assert fresh_inbox.recent_events[0].event_type == "comment_added"
        assert fresh_inbox.recent_events[0].message == "Human reviewer note."
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

        assert task.description == "Collect requirements, attach source files, and keep the task in plan."
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
