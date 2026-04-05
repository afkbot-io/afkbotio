"""Tests for the Task Flow service layer."""

from __future__ import annotations

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
        assert {item.id for item in summary.tasks} == {review_task.id, todo_task.id}
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
