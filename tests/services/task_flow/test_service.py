"""Tests for the Task Flow service layer."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from afkbot.db.session import session_scope
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from tests.repositories._harness import build_repository_factory

from afkbot.services.task_flow import TaskFlowServiceError
from afkbot.services.task_flow.service import TaskFlowService


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
            prompt="Prepare release notes from the current changelog.",
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
            prompt="Post the release announcement after notes are ready.",
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
            prompt="Review final copy and approve publishing.",
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
            prompt="Collect final media files.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            depends_on_task_ids=(review_task.id,),
        )
        todo_task = await service.create_task(
            profile_id="default",
            title="Send newsletter",
            prompt="Send the launch newsletter.",
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
            prompt="Review the AI draft and approve or request changes.",
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
            prompt="Send the final follow-up after review.",
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
            prompt="Review the AI-produced answer.",
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
            prompt="Recover the stale AI task after lease expiry.",
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
            prompt="Review the AI draft response.",
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
            prompt="Review directly as the human owner.",
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
            prompt="Should not appear in Alice inbox.",
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
            prompt="Review and approve the release draft.",
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
            prompt="Publish only after review completes.",
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
            prompt="Return this task for changes.",
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
            prompt="Finish the upstream prerequisite.",
            created_by_type="human",
            created_by_ref="cli",
        )
        leaf = await service.create_task(
            profile_id="default",
            title="Leaf task",
            prompt="Run after the root task.",
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
    )
    service = TaskFlowService(factory)
    now_utc = datetime.now(timezone.utc)
    try:
        prereq = await service.create_task(
            profile_id="default",
            title="Prerequisite",
            prompt="Complete prerequisite work first.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
        )
        todo_task = await service.create_task(
            profile_id="default",
            title="Todo overdue",
            prompt="Finish the overdue item.",
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
            prompt="Wait for prerequisite completion.",
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
            prompt="Wait for review before completion.",
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
            prompt="Already completed.",
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
            prompt="Already failed.",
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
            prompt="Already cancelled.",
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
            prompt="Claimed by background worker.",
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
            prompt="Running in background.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
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
            prompt="Collect execution history for operator review.",
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
            prompt="Work on a detached background task.",
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
            prompt="Draft the launch brief for operator review.",
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
            prompt="Collect the input metrics first.",
            created_by_type="human",
            created_by_ref="cli",
        )
        dependent = await service.create_task(
            profile_id="default",
            title="Publish metrics memo",
            prompt="Publish the memo after metrics are collected.",
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
            prompt="Review the release copy before publishing.",
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
            prompt="Review the task after the runtime finishes unrelated work.",
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
            prompt="Make sure inbox queries stay bounded.",
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
            prompt="Make sure concurrent inbox reads do not explode.",
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
            prompt="Review the drafted answer and leave precise notes.",
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
