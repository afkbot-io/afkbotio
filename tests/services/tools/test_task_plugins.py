"""Integration tests for Task Flow tool plugins."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.automation_repo import AutomationRepository
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.chat_session_turn_queue_repo import ChatSessionTurnQueueRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.profile_runtime import ProfileRuntimeConfig, get_profile_runtime_config_service
from afkbot.services.task_flow import get_task_flow_service, reset_task_flow_services_async
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


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


async def _prepare(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    *,
    taskflow_public_principal_required: bool = False,
) -> tuple[Settings, AsyncEngine, ToolRegistry]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tools_taskflow.db'}"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", db_url)
    get_settings.cache_clear()
    await reset_task_flow_services_async()

    settings = get_settings()
    settings.taskflow_public_principal_required = taskflow_public_principal_required
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        sessions = ChatSessionRepository(session)
        queue_repo = ChatSessionTurnQueueRepository(session)
        for session_id in (
            "s-task",
            "session-live-42",
            "task-seed",
            "s-maint",
            "s-task-maint",
            "s-review",
        ):
            await sessions.create(session_id=session_id, profile_id="default")
            await queue_repo.enqueue(
                profile_id="default",
                session_id=session_id,
                owner_token=f"pytest:default:{session_id}",
                source="pytest",
                client_msg_id=None,
            )
        await ProfileRepository(session).get_or_create_default("analyst")
        for session_id in ("taskflow:task_demo", "taskflow:analyst-demo"):
            await sessions.create(session_id=session_id, profile_id="analyst")
            await queue_repo.enqueue(
                profile_id="analyst",
                session_id=session_id,
                owner_token=f"pytest:analyst:{session_id}",
                source="pytest",
                client_msg_id=None,
            )

    return settings, engine, ToolRegistry.from_settings(settings)


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
    engine: AsyncEngine,
    *,
    profile_id: str,
    session_id: str,
    active: bool = True,
) -> None:
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default(profile_id)
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


async def _create_automation_actor(engine: AsyncEngine, *, profile_id: str, name: str) -> str:
    factory = create_session_factory(engine)
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


async def test_task_plugins_crud_roundtrip(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Task plugins should support create/list/get/update workflow."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s-task", run_id=1)

        create_tool = registry.get("task.create")
        assert create_tool is not None
        create_params = create_tool.parse_params(
            {
                "profile_key": "default",
                "title": "Prepare report",
                "description": "Compile the weekly report and store the summary.",
                "priority": 80,
                "labels": ["ops", "weekly"],
                "owner_type": "human",
                "owner_ref": "cli_user:alice",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        create_result = await create_tool.execute(ctx, create_params)
        assert create_result.ok is True
        task = create_result.payload["task"]
        assert isinstance(task, dict)
        assert task["status"] == "todo"
        assert task["owner_type"] == "human"
        assert task["owner_ref"] == "cli_user:alice"
        task_id = str(task["id"])

        inbox_tool = registry.get("task.inbox")
        comment_add_tool = registry.get("task.comment.add")
        comment_list_tool = registry.get("task.comment.list")
        assert inbox_tool is not None
        assert comment_add_tool is not None
        assert comment_list_tool is not None
        inbox_result = await inbox_tool.execute(
            ctx,
            inbox_tool.parse_params(
                {"profile_key": "default", "owner_ref": "cli_user:alice"},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert inbox_result.ok is True
        inbox = inbox_result.payload["inbox"]
        assert isinstance(inbox, dict)
        assert inbox["total_count"] == 1
        assert inbox["todo_count"] == 1
        assert inbox["overdue_count"] == 0
        assert inbox["unseen_event_count"] == 1
        assert inbox["recent_events"][0]["event_type"] == "created"

        inbox_mark_seen_result = await inbox_tool.execute(
            ctx,
            inbox_tool.parse_params(
                {
                    "profile_key": "default",
                    "owner_ref": "cli_user:alice",
                    "channel": "chat_startup",
                    "mark_seen": True,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert inbox_mark_seen_result.ok is False
        assert inbox_mark_seen_result.error_code == "task_inbox_mark_seen_not_allowed"

        comment_add_result = await comment_add_tool.execute(
            ctx,
            comment_add_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": task_id,
                    "message": "Add the missing KPI breakdown.",
                    "comment_type": "review_feedback",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert comment_add_result.ok is True
        task_comment = comment_add_result.payload["task_comment"]
        assert isinstance(task_comment, dict)
        assert task_comment["task_id"] == task_id
        assert task_comment["comment_type"] == "review_feedback"

        comment_list_result = await comment_list_tool.execute(
            ctx,
            comment_list_tool.parse_params(
                {"profile_key": "default", "task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert comment_list_result.ok is True
        task_comments = comment_list_result.payload["task_comments"]
        assert isinstance(task_comments, list)
        assert task_comments[0]["message"] == "Add the missing KPI breakdown."
        assert task_comments[0]["actor_type"] == "ai_profile"
        assert task_comments[0]["actor_ref"] == "default"

        list_tool = registry.get("task.list")
        assert list_tool is not None
        list_params = list_tool.parse_params(
            {"profile_key": "default", "owner_type": "human", "owner_ref": "cli_user:alice"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        list_result = await list_tool.execute(ctx, list_params)
        assert list_result.ok is True
        listed = list_result.payload["tasks"]
        assert isinstance(listed, list)
        assert len(listed) == 1
        assert listed[0]["id"] == task_id

        get_tool = registry.get("task.get")
        assert get_tool is not None
        get_params = get_tool.parse_params(
            {"profile_key": "default", "task_id": task_id},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        get_result = await get_tool.execute(ctx, get_params)
        assert get_result.ok is True
        fetched = get_result.payload["task"]
        assert isinstance(fetched, dict)
        assert fetched["title"] == "Prepare report"

        update_tool = registry.get("task.update")
        event_list_tool = registry.get("task.event.list")
        assert update_tool is not None
        assert event_list_tool is not None
        update_params = update_tool.parse_params(
            {
                "profile_key": "default",
                "task_id": task_id,
                "status": "review",
                "blocked_reason_code": "waiting_review",
                "blocked_reason_text": "Waiting for manual review.",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        update_result = await update_tool.execute(ctx, update_params)
        assert update_result.ok is True
        updated = update_result.payload["task"]
        assert isinstance(updated, dict)
        assert updated["status"] == "review"
        assert updated["blocked_reason_code"] == "waiting_review"

        event_list_result = await event_list_tool.execute(
            ctx,
            event_list_tool.parse_params(
                {"profile_key": "default", "task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert event_list_result.ok is True
        task_events = event_list_result.payload["task_events"]
        assert isinstance(task_events, list)
        assert task_events[0]["event_type"] == "updated"
        assert task_events[0]["actor_type"] == "ai_profile"
        assert task_events[0]["actor_ref"] == "default"
        assert {item["event_type"] for item in task_events} >= {"created", "comment_added", "updated"}

        prereq_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Collect source data",
                    "description": "Collect the source data before preparing the report.",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert prereq_result.ok is True
        prereq_task = prereq_result.payload["task"]
        assert isinstance(prereq_task, dict)
        prereq_id = str(prereq_task["id"])

        dependency_add_tool = registry.get("task.dependency.add")
        assert dependency_add_tool is not None
        dependency_add_result = await dependency_add_tool.execute(
            ctx,
            dependency_add_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": task_id,
                    "depends_on_task_id": prereq_id,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert dependency_add_result.ok is True
        dependency = dependency_add_result.payload["dependency"]
        assert isinstance(dependency, dict)
        assert dependency["task_id"] == task_id
        assert dependency["depends_on_task_id"] == prereq_id

        blocked_get_result = await get_tool.execute(
            ctx,
            get_tool.parse_params(
                {"profile_key": "default", "task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert blocked_get_result.ok is True
        blocked_task = blocked_get_result.payload["task"]
        assert isinstance(blocked_task, dict)
        assert blocked_task["status"] == "blocked"
        assert blocked_task["blocked_reason_code"] == "dependency_wait"
        assert blocked_task["depends_on_task_ids"] == [prereq_id]

        dependency_list_tool = registry.get("task.dependency.list")
        assert dependency_list_tool is not None
        dependency_list_result = await dependency_list_tool.execute(
            ctx,
            dependency_list_tool.parse_params(
                {"profile_key": "default", "task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert dependency_list_result.ok is True
        dependencies = dependency_list_result.payload["dependencies"]
        assert isinstance(dependencies, list)
        assert dependencies[0]["depends_on_task_id"] == prereq_id

        dependency_remove_tool = registry.get("task.dependency.remove")
        assert dependency_remove_tool is not None
        dependency_remove_result = await dependency_remove_tool.execute(
            ctx,
            dependency_remove_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": task_id,
                    "depends_on_task_id": prereq_id,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert dependency_remove_result.ok is True
        assert dependency_remove_result.payload["deleted"] is True

        unblocked_get_result = await get_tool.execute(
            ctx,
            get_tool.parse_params(
                {"profile_key": "default", "task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert unblocked_get_result.ok is True
        unblocked_task = unblocked_get_result.payload["task"]
        assert isinstance(unblocked_task, dict)
        assert unblocked_task["status"] == "todo"
        assert unblocked_task["depends_on_task_ids"] == []

        task_board_tool = registry.get("task.board")
        assert task_board_tool is not None
        task_board_result = await task_board_tool.execute(
            ctx,
            task_board_tool.parse_params(
                {
                    "profile_key": "default",
                    "owner_type": "human",
                    "owner_ref": "cli_user:alice",
                    "labels": ["weekly"],
                    "limit_per_column": 1,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert task_board_result.ok is True
        board = task_board_result.payload["board"]
        assert isinstance(board, dict)
        assert board["profile_id"] == "default"
        assert board["total_count"] == 1
        assert board["ready_count"] == 1
        running_column = next(column for column in board["columns"] if column["id"] == "running")
        assert running_column["count"] == 0
        todo_column = next(column for column in board["columns"] if column["id"] == "todo")
        assert todo_column["count"] == 1
        assert todo_column["tasks"][0]["id"] == task_id

        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            task_runs = TaskFlowRepository(session)
            task_run = await task_runs.create_task_run(
                task_id=task_id,
                attempt=1,
                owner_type="human",
                owner_ref="cli_user:alice",
                execution_mode="detached",
                status="review",
                session_id="taskflow:manual-demo",
                run_id=42,
                worker_id="taskflow-runtime:0",
                started_at=datetime.now(timezone.utc),
            )
            await task_runs.update_task_run(
                task_run_id=task_run.id,
                status="review",
                summary="Waiting for manual review.",
                error_code="awaiting_review",
                error_text="Waiting for manual review.",
                finished_at=datetime.now(timezone.utc),
            )

        task_run_list_tool = registry.get("task.run.list")
        assert task_run_list_tool is not None
        task_run_list_result = await task_run_list_tool.execute(
            ctx,
            task_run_list_tool.parse_params(
                {"profile_key": "default", "task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert task_run_list_result.ok is True
        task_runs_payload = task_run_list_result.payload["task_runs"]
        assert isinstance(task_runs_payload, list)
        assert task_runs_payload[0]["task_id"] == task_id
        task_run_id = int(task_runs_payload[0]["id"])

        task_run_list_profile_result = await task_run_list_tool.execute(
            ctx,
            task_run_list_tool.parse_params(
                {"profile_key": "default"},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert task_run_list_profile_result.ok is True
        task_runs_profile_payload = task_run_list_profile_result.payload["task_runs"]
        assert isinstance(task_runs_profile_payload, list)
        assert task_runs_profile_payload[0]["id"] == task_run_id

        task_run_get_tool = registry.get("task.run.get")
        assert task_run_get_tool is not None
        task_run_get_result = await task_run_get_tool.execute(
            ctx,
            task_run_get_tool.parse_params(
                {"profile_key": "default", "task_run_id": task_run_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert task_run_get_result.ok is True
        fetched_task_run = task_run_get_result.payload["task_run"]
        assert isinstance(fetched_task_run, dict)
        assert fetched_task_run["id"] == task_run_id
        assert fetched_task_run["error_code"] == "awaiting_review"

        flow_create_tool = registry.get("task.flow.create")
        assert flow_create_tool is not None
        flow_create_params = flow_create_tool.parse_params(
            {
                "profile_key": "default",
                "title": "Weekly operations",
                "description": "Flow for the weekly ops routine.",
                "default_owner_type": "human",
                "default_owner_ref": "cli_user:alice",
            },
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        flow_create_result = await flow_create_tool.execute(ctx, flow_create_params)
        assert flow_create_result.ok is True
        flow = flow_create_result.payload["task_flow"]
        assert isinstance(flow, dict)
        flow_id = str(flow["id"])

        flow_get_tool = registry.get("task.flow.get")
        assert flow_get_tool is not None
        flow_get_params = flow_get_tool.parse_params(
            {"profile_key": "default", "flow_id": flow_id},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        flow_get_result = await flow_get_tool.execute(ctx, flow_get_params)
        assert flow_get_result.ok is True
        fetched_flow = flow_get_result.payload["task_flow"]
        assert isinstance(fetched_flow, dict)
        assert fetched_flow["title"] == "Weekly operations"

        flow_list_tool = registry.get("task.flow.list")
        assert flow_list_tool is not None
        flow_list_params = flow_list_tool.parse_params(
            {"profile_key": "default"},
            default_timeout_sec=settings.tool_timeout_default_sec,
            max_timeout_sec=settings.tool_timeout_max_sec,
        )
        flow_list_result = await flow_list_tool.execute(ctx, flow_list_params)
        assert flow_list_result.ok is True
        listed_flows = flow_list_result.payload["task_flows"]
        assert isinstance(listed_flows, list)
        assert listed_flows[0]["id"] == flow_id
    finally:
        await engine.dispose()


async def test_task_create_plugin_requires_description_param(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`task.create` params schema should require description to match service contract."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        create_tool = registry.get("task.create")

        with pytest.raises(ValidationError, match="description"):
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Missing description",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            )
    finally:
        await engine.dispose()


async def test_task_delegate_plugin_requires_description_param(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`task.delegate` params schema should require description to match service contract."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        delegate_tool = registry.get("task.delegate")

        with pytest.raises(ValidationError, match="description"):
            delegate_tool.parse_params(
                {
                    "profile_key": "default",
                    "owner_ref": "papercliper",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            )
    finally:
        await engine.dispose()


async def test_task_update_plugin_binds_current_session_for_running_status(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`task.update` should bind the current tool session when a task is marked running."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="session-live-42", run_id=1)

        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        assert create_tool is not None
        assert update_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Bind session",
                    "description": "Attach the current session when work starts.",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        update_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": task_id,
                    "status": "running",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert update_result.ok is True
        updated = update_result.payload["task"]
        assert isinstance(updated, dict)
        assert updated["status"] == "running"
        assert updated["last_session_id"] == "session-live-42"
        assert updated["last_session_profile_id"] == "default"
    finally:
        await engine.dispose()


async def test_task_update_plugin_schedules_blocked_revisit_from_retry_after_sec(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`task.update` should allow blocked tasks to opt into a delayed revisit."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="session-live-42", run_id=1)

        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        assert create_tool is not None
        assert update_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Poll vendor status",
                    "description": "Recheck the external vendor later.",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        before_update = datetime.now(timezone.utc)
        update_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": task_id,
                    "status": "blocked",
                    "blocked_reason_code": "vendor_pending",
                    "blocked_reason_text": "The external vendor has not replied yet.",
                    "retry_after_sec": 7200,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert update_result.ok is True
        updated = update_result.payload["task"]
        assert isinstance(updated, dict)
        assert updated["status"] == "blocked"
        assert updated["ready_at"] is not None
        ready_at = datetime.fromisoformat(str(updated["ready_at"]))
        if ready_at.tzinfo is None:
            ready_at = ready_at.replace(tzinfo=timezone.utc)
        assert ready_at >= before_update + timedelta(minutes=119)
    finally:
        await engine.dispose()


async def test_task_update_plugin_forwards_explicit_ready_at_null(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`task.update` should propagate explicit `ready_at=null` to clear scheduled revisit state."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        service = get_task_flow_service(settings)
        task = await service.create_task(
            profile_id="default",
            title="Scheduled retry to clear",
            description="Clear explicit ready_at via task.update.",
            created_by_type="human",
            created_by_ref="cli",
        )
        await service.update_task(
            profile_id="default",
            task_id=task.id,
            status="blocked",
            blocked_reason_code="blocked_on_dependency",
            actor_type="human",
            actor_ref="cli",
            ready_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )

        update_tool = registry.get("task.update")
        get_tool = registry.get("task.get")
        assert update_tool is not None
        assert get_tool is not None

        ctx = ToolContext(
            profile_id="default",
            session_id="tool-session",
            run_id=1,
        )

        clear_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": task.id,
                    "ready_at": None,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert clear_result.ok is True
        assert clear_result.payload["task"]["ready_at"] is None

        fetched_result = await get_tool.execute(
            ctx,
            get_tool.parse_params(
                {"profile_key": "default", "task_id": task.id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert fetched_result.ok is True
        assert fetched_result.payload["task"]["ready_at"] is None
    finally:
        await engine.dispose()


async def test_task_update_plugin_rejects_explicit_foreign_session_binding(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """AI actors should not bind a task to some other live session id."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    factory = create_session_factory(engine)
    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("papercliper",),
        )

        ctx = ToolContext(profile_id="default", session_id="session-live-42", run_id=1)

        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        assert create_tool is not None
        assert update_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Bind delegated session",
                    "description": "Attach a delegated execution session.",
                    "owner_type": "ai_profile",
                    "owner_ref": "papercliper",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        update_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": task_id,
                    "status": "running",
                    "session_id": "papercliper-main",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert update_result.ok is False
        assert update_result.error_code == "task_session_binding_forbidden"
    finally:
        await engine.dispose()


async def test_task_update_plugin_rejects_explicit_session_binding_in_automation_graph_runtime(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """automation graph runtime must not let generic task.update override session bindings."""

    settings, engine, registry = await _prepare(
        tmp_path,
        monkeypatch,
        taskflow_public_principal_required=True,
    )
    try:
        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        assert create_tool is not None
        assert update_tool is not None

        seed_ctx = ToolContext(profile_id="default", session_id="session-live-42", run_id=1)
        create_result = await create_tool.execute(
            seed_ctx,
            create_tool.parse_params(
                {
                    "title": "Automation runtime update target",
                    "description": "Seed task for automation graph update guard.",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        automation_ctx = ToolContext(
            profile_id="default",
            session_id="automation-graph-42-9-call_tool",
            run_id=9,
            runtime_metadata={
                "automation_graph": {
                    "automation_id": 42,
                    "run_id": 9,
                    "node_key": "call_tool",
                    "trigger_type": "webhook",
                }
            },
        )
        update_result = await update_tool.execute(
            automation_ctx,
            update_tool.parse_params(
                {
                    "task_id": task_id,
                    "status": "running",
                    "session_id": "borrowed-session",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert update_result.ok is False
        assert update_result.error_code == "task_session_binding_forbidden"
    finally:
        await engine.dispose()


async def test_task_block_plugin_uses_runtime_task_context_and_schedules_revisit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`task.block` should default to the runtime task and expose scheduled retry metadata."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        create_tool = registry.get("task.create")
        block_tool = registry.get("task.block")
        get_tool = registry.get("task.get")
        assert create_tool is not None
        assert block_tool is not None
        assert get_tool is not None

        operator_ctx = ToolContext(profile_id="default", session_id="task-seed", run_id=13)
        create_result = await create_tool.execute(
            operator_ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Recheck vendor reply",
                    "description": "Work this task until the vendor answers.",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])
        await _create_chat_session(
            engine,
            profile_id="default",
            session_id=f"taskflow:{task_id}",
        )

        ctx = ToolContext(
            profile_id="default",
            session_id=f"taskflow:{task_id}",
            run_id=14,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "task_id": task_id,
                    "task_profile_id": "default",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                },
            },
        )

        before_block = datetime.now(timezone.utc)
        block_result = await block_tool.execute(
            ctx,
            block_tool.parse_params(
                {
                    "reason_code": "vendor_pending",
                    "reason_text": "The external vendor has not replied yet.",
                    "retry_after_sec": 7200,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert block_result.ok is True
        blocked_task = block_result.payload["task"]
        assert isinstance(blocked_task, dict)
        assert blocked_task["id"] == task_id
        assert blocked_task["status"] == "blocked"
        assert blocked_task["block_state"]["kind"] == "scheduled_retry"
        assert blocked_task["block_state"]["retry_scheduled"] is True
        ready_at = datetime.fromisoformat(str(blocked_task["ready_at"]))
        if ready_at.tzinfo is None:
            ready_at = ready_at.replace(tzinfo=timezone.utc)
        assert ready_at >= before_block + timedelta(minutes=119)

        fetched_result = await get_tool.execute(
            operator_ctx,
            get_tool.parse_params(
                {"profile_key": "default", "task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert fetched_result.ok is True
        assert fetched_result.payload["task"]["block_state"]["kind"] == "scheduled_retry"
    finally:
        await engine.dispose()


async def test_task_block_plugin_rejects_timed_dependency_wait(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`task.block` should reject dependency_wait blockers with a timer."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="session-live-42", run_id=15)

        create_tool = registry.get("task.create")
        block_tool = registry.get("task.block")
        assert create_tool is not None
        assert block_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Wait for teammate result",
                    "description": "Pause until the delegated teammate finishes.",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        block_result = await block_tool.execute(
            ctx,
            block_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": task_id,
                    "reason_code": "dependency_wait",
                    "reason_text": "Waiting for the delegated task to finish.",
                    "retry_after_sec": 3600,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert block_result.ok is False
        assert block_result.error_code == "task_dependency_wait_ready_at_conflict"
    finally:
        await engine.dispose()


async def test_task_block_plugin_supports_structured_ai_subagent_reassignment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.block should accept structured ai_subagent owner reassignment inputs."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-only subagent.",
        )

        ctx = ToolContext(profile_id="default", session_id="session-live-42", run_id=16)
        create_tool = registry.get("task.create")
        block_tool = registry.get("task.block")
        assert create_tool is not None
        assert block_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Blocked for structured reassignment",
                    "description": "Block and send to the reviewer subagent.",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        block_result = await block_tool.execute(
            ctx,
            block_tool.parse_params(
                {
                    "task_id": task_id,
                    "reason_code": "needs_review",
                    "reason_text": "Handing this off to the review specialist.",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert block_result.ok is True
        task_payload = block_result.payload["task"]
        assert task_payload["status"] == "blocked"
        assert task_payload["owner_type"] == "ai_subagent"
        assert task_payload["owner_ref"] == "papercliper:reviewer"
    finally:
        await engine.dispose()


async def test_task_maintenance_sweep_plugin_repairs_stale_claims(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Maintenance sweep tool should release stale claims back to todo within the scoped profile."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    factory = create_session_factory(engine)
    try:
        ctx = ToolContext(profile_id="default", session_id="s-maint", run_id=1)
        create_tool = registry.get("task.create")
        maintenance_tool = registry.get("task.stale.sweep")
        assert create_tool is not None
        assert maintenance_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Repair stale claim",
                    "description": "Repair a stale claim via explicit maintenance.",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task = create_result.payload["task"]
        task_id = str(task["id"])

        stale_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed = await repo.claim_next_runnable_task(
                now_utc=stale_now,
                lease_until=stale_now - timedelta(minutes=5),
                claim_token="plugin-stale-claim",
                claimed_by="taskflow-runtime:plugin",
            )
            assert claimed is not None
            task_run = await repo.create_task_run(
                task_id=task_id,
                attempt=claimed.current_attempt,
                owner_type=claimed.owner_type,
                owner_ref=claimed.owner_ref,
                execution_mode="detached",
                status="running",
                session_id=f"taskflow:{task_id}",
                run_id=None,
                worker_id="taskflow-runtime:plugin",
                started_at=stale_now - timedelta(minutes=10),
            )
            attached = await repo.attach_task_run(
                task_id=task_id,
                claim_token="plugin-stale-claim",
                task_run_id=task_run.id,
                session_id=f"taskflow:{task_id}",
            )
            assert attached is True
            started = await repo.mark_task_started(
                task_id=task_id,
                claim_token="plugin-stale-claim",
                started_at=stale_now - timedelta(minutes=10),
            )
            assert started is True

        maintenance_result = await maintenance_tool.execute(
            ctx,
            maintenance_tool.parse_params(
                {"profile_key": "default"},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert maintenance_result.ok is True
        maintenance = maintenance_result.payload["maintenance"]
        assert maintenance["repaired_count"] == 1
        assert maintenance["remaining_count"] == 0
        assert maintenance["remaining"] == []

        async with session_scope(factory) as session:
            repaired = await TaskFlowRepository(session).get_task(
                profile_id="default",
                task_id=task_id,
            )
            assert repaired is not None
            assert repaired.status == "todo"
            assert repaired.last_error_code == "task_lease_expired"
    finally:
        await engine.dispose()


async def test_task_plugins_task_create_uses_runtime_session_principal_when_guard_required(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.create should pass strict public-principal checks in taskflow runtime context."""

    settings, engine, registry = await _prepare(
        tmp_path,
        monkeypatch,
        taskflow_public_principal_required=True,
    )
    try:
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("analyst",),
        )

        create_tool = registry.get("task.create")
        get_tool = registry.get("task.get")
        assert create_tool is not None
        assert get_tool is not None

        ctx = ToolContext(
            profile_id="analyst",
            session_id="taskflow:task_demo",
            run_id=7,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "task_profile_id": "default",
                    "task_id": "task_demo",
                },
            },
        )

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Runtime backlog note",
                    "description": "Keep backlog changes in manager profile.",
                    "owner_type": "human",
                    "owner_ref": "cli_user:alice",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_payload = create_result.payload["task"]
        assert isinstance(task_payload, dict)
        task_id = str(task_payload["id"])
        assert task_payload["profile_id"] == "default"

        get_result = await get_tool.execute(
            ctx,
            get_tool.parse_params(
                {"task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert get_result.ok is True
        fetched = get_result.payload["task"]
        assert isinstance(fetched, dict)
        assert fetched["created_by_type"] == "ai_profile"
        assert fetched["created_by_ref"] == "analyst"
        assert fetched["profile_id"] == "default"
    finally:
        await engine.dispose()


async def test_task_create_runtime_scope_binds_session_profile_to_task_profile_by_default(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Runtime-scoped task.create should persist session profile binding to task profile."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")
        _write_team_runtime_config(settings=settings, profile_id="default", team_profile_ids=("analyst",))

        ctx = ToolContext(
            profile_id="analyst",
            session_id="taskflow:task_demo",
            run_id=7,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "task_id": "task_demo",
                    "task_profile_id": "default",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
            },
        )
        create_tool = registry.get("task.create")
        get_tool = registry.get("task.get")
        assert create_tool is not None
        assert get_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Runtime scoped create",
                    "description": "Follow the runtime principal guard",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert create_result.ok is True
        task_payload = create_result.payload["task"]
        task_id = str(task_payload["id"])
        assert task_payload["last_session_id"] == "taskflow:task_demo"
        assert task_payload["last_session_profile_id"] == "default"

        get_result = await get_tool.execute(
            ctx,
            get_tool.parse_params(
                {"task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert get_result.ok is True
        fetched = get_result.payload["task"]
        assert fetched["last_session_id"] == "taskflow:task_demo"
        assert fetched["last_session_profile_id"] == "default"
    finally:
        await engine.dispose()


async def test_task_create_runtime_scope_rejects_cross_profile_session_binding(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Runtime-scoped task.create should reject explicit cross-profile session binding."""

    settings, engine, registry = await _prepare(
        tmp_path,
        monkeypatch,
        taskflow_public_principal_required=True,
    )
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")
        _write_team_runtime_config(settings=settings, profile_id="default", team_profile_ids=("analyst",))

        ctx = ToolContext(
            profile_id="analyst",
            session_id="taskflow:task_demo",
            run_id=7,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "task_id": "task_demo",
                    "task_profile_id": "default",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
            },
        )
        create_tool = registry.get("task.create")
        flow_tool = registry.get("task.flow.create")
        list_tool = registry.get("task.list")
        assert create_tool is not None
        assert flow_tool is not None
        assert list_tool is not None

        flow_result = await flow_tool.execute(
            ctx,
            flow_tool.parse_params(
                {
                    "title": "Cross profile reject flow",
                    "default_owner_type": "ai_profile",
                    "default_owner_ref": "analyst",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert flow_result.ok is True
        flow_id = str(flow_result.payload["task_flow"]["id"])

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "flow_id": flow_id,
                    "title": "Cross profile create attempt",
                    "description": "should fail because explicit session_profile_id mismatches runtime profile",
                    "session_profile_id": "analyst",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert create_result.ok is False
        assert create_result.error_code == "task_session_binding_forbidden"

        list_result = await list_tool.execute(
            ctx,
            list_tool.parse_params(
                {
                    "flow_id": flow_id,
                    "statuses": [],
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert list_result.ok is True
        tasks = list_result.payload["tasks"]
        assert isinstance(tasks, list)
        assert tasks == []
    finally:
        await engine.dispose()


async def test_task_create_plugin_rejects_explicit_session_binding_in_automation_graph_runtime(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """automation graph runtime must not let generic task.create override session bindings."""

    settings, engine, registry = await _prepare(
        tmp_path,
        monkeypatch,
        taskflow_public_principal_required=True,
    )
    try:
        create_tool = registry.get("task.create")
        assert create_tool is not None

        ctx = ToolContext(
            profile_id="default",
            session_id="automation-graph-42-7-call_tool",
            run_id=7,
            runtime_metadata={
                "automation_graph": {
                    "automation_id": 42,
                    "run_id": 7,
                    "node_key": "call_tool",
                    "trigger_type": "webhook",
                }
            },
        )

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Automation runtime create",
                    "description": "should reject explicit task session binding",
                    "session_id": "borrowed-session",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert create_result.ok is False
        assert create_result.error_code == "task_session_binding_forbidden"
    finally:
        await engine.dispose()


async def test_task_create_plugin_uses_automation_principal_without_fake_session_binding(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """automation graph task.create should not persist a synthetic task lease session."""

    settings, engine, registry = await _prepare(
        tmp_path,
        monkeypatch,
        taskflow_public_principal_required=True,
    )
    try:
        actor_ref = await _create_automation_actor(
            engine,
            profile_id="default",
            name="task-create-automation-actor",
        )
        automation_id = int(actor_ref.rsplit(":", 1)[1])
        create_tool = registry.get("task.create")
        assert create_tool is not None

        ctx = ToolContext(
            profile_id="default",
            session_id="automation-graph-actor-create",
            run_id=8,
            runtime_metadata={
                "automation_graph": {
                    "automation_id": automation_id,
                    "run_id": 8,
                    "node_key": "create_task",
                    "trigger_type": "webhook",
                }
            },
        )

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Automation created task",
                    "description": "created without borrowing a chat/task session",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert create_result.ok is True
        task_payload = create_result.payload["task"]
        assert isinstance(task_payload, dict)
        assert task_payload["created_by_type"] == "automation"
        assert task_payload["created_by_ref"] == actor_ref
        assert task_payload["last_session_id"] is None
        assert task_payload["last_session_profile_id"] is None
    finally:
        await engine.dispose()


async def test_task_update_plugin_rejects_running_status_without_real_session_in_automation_graph(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """automation graph task.update must not synthesize claimed/running task leases."""

    settings, engine, registry = await _prepare(
        tmp_path,
        monkeypatch,
        taskflow_public_principal_required=True,
    )
    try:
        actor_ref = await _create_automation_actor(
            engine,
            profile_id="default",
            name="task-update-automation-actor",
        )
        automation_id = int(actor_ref.rsplit(":", 1)[1])
        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        assert create_tool is not None
        assert update_tool is not None

        ctx = ToolContext(
            profile_id="default",
            session_id="automation-graph-actor-update",
            run_id=9,
            runtime_metadata={
                "automation_graph": {
                    "automation_id": automation_id,
                    "run_id": 9,
                    "node_key": "task_update",
                    "trigger_type": "webhook",
                }
            },
        )

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Task awaiting AI worker",
                    "description": "automation can create it, but cannot fake a live worker lease",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_payload = create_result.payload["task"]
        assert isinstance(task_payload, dict)

        update_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "task_id": str(task_payload["id"]),
                    "status": "running",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert update_result.ok is False
        assert update_result.error_code == "task_session_binding_forbidden"
    finally:
        await engine.dispose()


async def test_task_plugins_runtime_profile_scope_ignores_explicit_profile_target_by_default(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Runtime task_profile_id should stay authoritative when explicit profile target differs."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")

        ctx = ToolContext(
            profile_id="analyst",
            session_id="session-live-42",
            run_id=1,
            runtime_metadata={
                "taskflow": {
                    "task_id": "task_demo",
                    "task_profile_id": "default",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
            },
        )
        create_tool = registry.get("task.create")
        assert create_tool is not None

        explicit_targets = ({"profile_id": "analyst"}, {"profile_key": "analyst"})
        for index, explicit_target in enumerate(explicit_targets, start=1):
            create_result = await create_tool.execute(
                ctx,
                create_tool.parse_params(
                    {
                        **explicit_target,
                        "title": f"Runtime backlog note #{index}",
                        "description": "Keep backlog changes in manager profile.",
                        "owner_type": "human",
                        "owner_ref": "cli_user:alice",
                    },
                    default_timeout_sec=settings.tool_timeout_default_sec,
                    max_timeout_sec=settings.tool_timeout_max_sec,
                ),
            )
            assert create_result.ok is True
            task_payload = create_result.payload["task"]
            assert isinstance(task_payload, dict)
            assert task_payload["profile_id"] == "default"
    finally:
        await engine.dispose()


async def test_task_plugins_runtime_profile_scope_allows_explicit_profile_target_with_flag(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Explicit profile override should require an opt-in feature flag in runtime scope."""

    monkeypatch.setenv("AFKBOT_TASKFLOW_ALLOW_RUNTIME_PROFILE_OVERRIDE", "1")
    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")

        ctx = ToolContext(
            profile_id="analyst",
            session_id="session-live-42",
            run_id=1,
            runtime_metadata={
                "taskflow": {
                    "task_id": "task_demo",
                    "task_profile_id": "default",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
            },
        )
        create_tool = registry.get("task.create")
        assert create_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_id": "analyst",
                    "title": "Runtime override note",
                    "description": "Allow explicit profile target via guarded override.",
                    "owner_type": "human",
                    "owner_ref": "cli_user:alice",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_payload = create_result.payload["task"]
        assert isinstance(task_payload, dict)
        assert task_payload["profile_id"] == "analyst"
    finally:
        await engine.dispose()


async def test_task_plugins_allow_agent_to_delegate_task_to_another_ai_profile(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Runtime-scoped agents should be able to create backlog tasks owned by another AI profile."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("analyst", "papercliper"),
        )

        ctx = ToolContext(
            profile_id="analyst",
            session_id="taskflow:task_demo",
            run_id=8,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "task_id": "task_demo",
                    "task_profile_id": "default",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
            },
        )

        create_tool = registry.get("task.create")
        assert create_tool is not None
        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Papercliper follow-up",
                    "description": "Take over the research-heavy follow-up work.",
                    "owner_type": "ai_profile",
                    "owner_ref": "papercliper",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task = create_result.payload["task"]
        assert isinstance(task, dict)
        assert task["profile_id"] == "default"
        assert task["owner_type"] == "ai_profile"
        assert task["owner_ref"] == "papercliper"
        assert task["created_by_type"] == "ai_profile"
        assert task["created_by_ref"] == "analyst"
    finally:
        await engine.dispose()


async def test_task_delegate_plugin_uses_runtime_task_context_by_default(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """`task.delegate` should default to the current runtime task when task_id is omitted."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("analyst", "papercliper"),
        )

        create_tool = registry.get("task.create")
        delegate_tool = registry.get("task.delegate")
        get_tool = registry.get("task.get")
        assert create_tool is not None
        assert delegate_tool is not None
        assert get_tool is not None

        operator_ctx = ToolContext(profile_id="default", session_id="task-seed", run_id=11)
        parent_result = await create_tool.execute(
            operator_ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Own launch brief",
                    "description": "Prepare the launch brief and delegate research.",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert parent_result.ok is True
        parent_task = parent_result.payload["task"]
        parent_task_id = str(parent_task["id"])

        ctx = ToolContext(
            profile_id="analyst",
            session_id="taskflow:task_demo",
            run_id=12,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "task_id": parent_task_id,
                    "task_profile_id": "default",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
            },
        )
        delegate_result = await delegate_tool.execute(
            ctx,
            delegate_tool.parse_params(
                {
                    "description": "Research competitor pricing and summarize the deltas.",
                    "owner_ref": "papercliper",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert delegate_result.ok is True
        delegation = delegate_result.payload["delegation"]
        assert delegation["source_task"]["id"] == parent_task_id
        assert delegation["source_task"]["status"] == "blocked"
        assert delegation["delegated_task"]["owner_ref"] == "papercliper"
        assert delegation["delegated_task"]["source_type"] == "task_delegation"
        assert delegation["dependency"]["task_id"] == parent_task_id

        refreshed_parent = await get_tool.execute(
            operator_ctx,
            get_tool.parse_params(
                {"profile_key": "default", "task_id": parent_task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert refreshed_parent.ok is True
        task_payload = refreshed_parent.payload["task"]
        assert task_payload["status"] == "blocked"
        assert task_payload["blocked_reason_code"] == "dependency_wait"
        assert task_payload["depends_on_task_ids"] == [delegation["delegated_task"]["id"]]
    finally:
        await engine.dispose()


async def test_task_plugins_allow_subagent_runtime_to_delegate_task_to_ai_subagent(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Runtime subagents should keep ai_subagent actor identity when delegating work."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="analyst",
            subagent_name="researcher",
            markdown="# Researcher\nResearch-only subagent.",
        )
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-heavy subagent.",
        )
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("analyst", "papercliper"),
        )

        create_tool = registry.get("task.create")
        delegate_tool = registry.get("task.delegate")
        assert create_tool is not None
        assert delegate_tool is not None

        operator_ctx = ToolContext(profile_id="default", session_id="task-seed", run_id=21)
        parent_result = await create_tool.execute(
            operator_ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Own launch brief",
                    "description": "Prepare the launch brief and delegate review research.",
                    "owner_type": "ai_subagent",
                    "owner_ref": "analyst:researcher",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert parent_result.ok is True
        parent_task = parent_result.payload["task"]
        parent_task_id = str(parent_task["id"])

        ctx = ToolContext(
            profile_id="analyst",
            session_id="taskflow:task_demo",
            run_id=22,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "task_id": parent_task_id,
                    "task_profile_id": "default",
                    "owner_type": "ai_subagent",
                    "owner_ref": "analyst:researcher",
                },
            },
            trusted_runtime_context={
                "taskflow_detached_runtime": {
                    "owner_type": "ai_subagent",
                    "owner_ref": "analyst:researcher",
                }
            },
        )
        delegate_result = await delegate_tool.execute(
            ctx,
            delegate_tool.parse_params(
                {
                    "description": "Review the launch brief assumptions and call out gaps.",
                    "owner_type": "ai_subagent",
                    "owner_ref": "papercliper:reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert delegate_result.ok is True
        delegation = delegate_result.payload["delegation"]
        assert delegation["delegated_task"]["owner_type"] == "ai_subagent"
        assert delegation["delegated_task"]["owner_ref"] == "papercliper:reviewer"
        assert delegation["delegated_task"]["created_by_type"] == "ai_subagent"
        assert delegation["delegated_task"]["created_by_ref"] == "analyst:researcher"
    finally:
        await engine.dispose()


async def test_task_create_plugin_supports_structured_ai_subagent_assignment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.create should accept structured ai_subagent owner inputs and infer owner_type."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-only subagent.",
        )

        create_tool = registry.get("task.create")
        assert create_tool is not None

        result = await create_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=31),
            create_tool.parse_params(
                {
                    "title": "Structured subagent assignment",
                    "description": "Assign directly to one subagent without manual owner_ref.",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert result.ok is True
        task_payload = result.payload["task"]
        assert task_payload["owner_type"] == "ai_subagent"
        assert task_payload["owner_ref"] == "papercliper:reviewer"
    finally:
        await engine.dispose()


async def test_task_create_plugin_supports_structured_ai_profile_assignment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.create should accept structured profile-only owner/reviewer inputs and infer ai_profile."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
            await ProfileRepository(session).get_or_create_default("reviewer")

        create_tool = registry.get("task.create")
        assert create_tool is not None

        result = await create_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=31),
            create_tool.parse_params(
                {
                    "title": "Structured profile assignment",
                    "description": "Assign directly to one orchestrator profile without manual owner_ref.",
                    "owner_profile_id": "papercliper",
                    "reviewer_profile_id": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert result.ok is True
        task_payload = result.payload["task"]
        assert task_payload["owner_type"] == "ai_profile"
        assert task_payload["owner_ref"] == "papercliper"
        assert task_payload["reviewer_type"] == "ai_profile"
        assert task_payload["reviewer_ref"] == "reviewer"
    finally:
        await engine.dispose()


async def test_task_update_plugin_supports_structured_ai_subagent_reassignment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.update should accept structured ai_subagent owner inputs when reassigning a task."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-only subagent.",
        )

        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        assert create_tool is not None
        assert update_tool is not None

        create_result = await create_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=32),
            create_tool.parse_params(
                {
                    "title": "Need reassignment",
                    "description": "Start on default owner first.",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        update_result = await update_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=33),
            update_tool.parse_params(
                {
                    "task_id": task_id,
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert update_result.ok is True
        task_payload = update_result.payload["task"]
        assert task_payload["owner_type"] == "ai_subagent"
        assert task_payload["owner_ref"] == "papercliper:reviewer"
    finally:
        await engine.dispose()


async def test_task_delegate_plugin_supports_structured_ai_subagent_assignment(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.delegate should accept structured ai_subagent target inputs and infer owner_type."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-only subagent.",
        )
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("analyst", "papercliper"),
        )

        create_tool = registry.get("task.create")
        delegate_tool = registry.get("task.delegate")
        assert create_tool is not None
        assert delegate_tool is not None

        parent_result = await create_tool.execute(
            ToolContext(profile_id="default", session_id="task-seed", run_id=34),
            create_tool.parse_params(
                {
                    "title": "Parent task",
                    "description": "Delegate structured subagent follow-up.",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert parent_result.ok is True
        parent_task_id = str(parent_result.payload["task"]["id"])

        delegate_result = await delegate_tool.execute(
            ToolContext(
                profile_id="analyst",
                session_id="taskflow:task_demo",
                run_id=35,
                runtime_metadata={
                    "transport": "taskflow",
                    "taskflow": {
                        "task_id": parent_task_id,
                        "task_profile_id": "default",
                        "owner_type": "ai_profile",
                        "owner_ref": "analyst",
                    },
                },
            ),
            delegate_tool.parse_params(
                {
                    "description": "Hand off review work to the reviewer subagent.",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert delegate_result.ok is True
        delegation = delegate_result.payload["delegation"]
        assert delegation["delegated_task"]["owner_type"] == "ai_subagent"
        assert delegation["delegated_task"]["owner_ref"] == "papercliper:reviewer"
    finally:
        await engine.dispose()


async def test_task_flow_create_plugin_supports_structured_ai_subagent_default_owner(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.flow.create should accept structured ai_subagent default-owner inputs."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-only subagent.",
        )

        flow_tool = registry.get("task.flow.create")
        assert flow_tool is not None

        result = await flow_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=36),
            flow_tool.parse_params(
                {
                    "title": "Structured default-owner flow",
                    "default_owner_profile_id": "papercliper",
                    "default_owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert result.ok is True
        flow_payload = result.payload["task_flow"]
        assert flow_payload["default_owner_type"] == "ai_subagent"
        assert flow_payload["default_owner_ref"] == "papercliper:reviewer"
    finally:
        await engine.dispose()


async def test_task_flow_create_plugin_supports_structured_ai_profile_default_owner(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.flow.create should accept structured profile-only default-owner inputs."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")

        flow_tool = registry.get("task.flow.create")
        assert flow_tool is not None

        result = await flow_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=36),
            flow_tool.parse_params(
                {
                    "title": "Structured profile default-owner flow",
                    "default_owner_profile_id": "papercliper",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert result.ok is True
        flow_payload = result.payload["task_flow"]
        assert flow_payload["default_owner_type"] == "ai_profile"
        assert flow_payload["default_owner_ref"] == "papercliper"
    finally:
        await engine.dispose()


async def test_task_create_plugin_accepts_equivalent_raw_and_structured_subagent_owner_inputs(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.create should accept migration-period duplicate owner selectors when equivalent."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        _write_profile_subagent(
            settings=settings,
            profile_id="default",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-only subagent.",
        )

        create_tool = registry.get("task.create")
        assert create_tool is not None

        result = await create_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=37),
            create_tool.parse_params(
                {
                    "title": "Equivalent owner selectors",
                    "description": "Accept equivalent raw and structured selectors during migration.",
                    "owner_type": "ai_subagent",
                    "owner_ref": " default:Reviewer ",
                    "owner_profile_id": "default",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert result.ok is True
        task_payload = result.payload["task"]
        assert task_payload["owner_type"] == "ai_subagent"
        assert task_payload["owner_ref"] == "default:reviewer"
    finally:
        await engine.dispose()


async def test_task_create_plugin_rejects_mismatched_raw_and_structured_subagent_owner_inputs(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.create should reject duplicate owner selectors when raw and structured values diverge."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        create_tool = registry.get("task.create")
        assert create_tool is not None

        result = await create_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=38),
            create_tool.parse_params(
                {
                    "title": "Mismatched owner selectors",
                    "description": "Reject conflicting raw and structured selectors.",
                    "owner_type": "ai_subagent",
                    "owner_ref": "default:reviewer",
                    "owner_profile_id": "default",
                    "owner_subagent_name": "researcher",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert result.ok is False
        assert result.error_code == "invalid_owner_ref"
        assert "conflicts with" in (result.reason or "")
    finally:
        await engine.dispose()


async def test_task_create_plugin_rejects_invalid_structured_subagent_owner_inputs(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.create should fail closed when structured subagent owner inputs are invalid."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        create_tool = registry.get("task.create")
        assert create_tool is not None

        result = await create_tool.execute(
            ToolContext(profile_id="default", session_id="s-task", run_id=38),
            create_tool.parse_params(
                {
                    "title": "Invalid structured owner selector",
                    "description": "Do not allow invalid profile ids through structured subagent fields.",
                    "owner_profile_id": "invalid profile",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert result.ok is False
        assert result.error_code == "invalid_owner_ref"
    finally:
        await engine.dispose()


async def test_task_update_plugin_rejects_coworker_task_mutation(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """One runtime worker should not mutate another worker's backlog task."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("analyst", "papercliper"),
        )

        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        assert create_tool is not None
        assert update_tool is not None

        operator_ctx = ToolContext(profile_id="default", session_id="task-seed", run_id=21)
        create_result = await create_tool.execute(
            operator_ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Papercliper owned task",
                    "description": "This task belongs to Papercliper.",
                    "owner_type": "ai_profile",
                    "owner_ref": "papercliper",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        analyst_ctx = ToolContext(
            profile_id="analyst",
            session_id="taskflow:analyst-demo",
            run_id=22,
            runtime_metadata={
                "transport": "taskflow",
                "taskflow": {
                    "task_id": "task_demo",
                    "task_profile_id": "default",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
            },
        )
        update_result = await update_tool.execute(
            analyst_ctx,
            update_tool.parse_params(
                {
                    "task_id": task_id,
                    "status": "running",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert update_result.ok is False
        assert update_result.error_code == "task_actor_forbidden"
    finally:
        await engine.dispose()


async def test_task_plugins_list_and_repair_stale_claims(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Operator tools should inspect and repair stale Task Flow claims safely."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s-task-maint", run_id=9)
        create_tool = registry.get("task.create")
        stale_list_tool = registry.get("task.stale.list")
        maintenance_tool = registry.get("task.stale.sweep")
        event_list_tool = registry.get("task.event.list")
        assert create_tool is not None
        assert stale_list_tool is not None
        assert maintenance_tool is not None
        assert event_list_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Recover stale claim",
                    "description": "Repair the stale claim and retry the task.",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task = create_result.payload["task"]
        assert isinstance(task, dict)
        task_id = str(task["id"])

        factory = create_session_factory(engine)
        stale_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            claimed = await repo.claim_next_runnable_task(
                now_utc=stale_now,
                lease_until=stale_now - timedelta(minutes=2),
                claim_token="tool-stale-claim",
                claimed_by="taskflow-runtime:stale-tool",
            )
            assert claimed is not None
            task_run = await repo.create_task_run(
                task_id=task_id,
                attempt=claimed.current_attempt,
                owner_type=claimed.owner_type,
                owner_ref=claimed.owner_ref,
                execution_mode="detached",
                status="running",
                session_id=f"taskflow:{task_id}",
                run_id=None,
                worker_id="taskflow-runtime:stale-tool",
                started_at=stale_now - timedelta(minutes=3),
            )
            attached = await repo.attach_task_run(
                task_id=task_id,
                claim_token="tool-stale-claim",
                task_run_id=task_run.id,
                session_id=f"taskflow:{task_id}",
            )
            assert attached is True
            started = await repo.mark_task_started(
                task_id=task_id,
                claim_token="tool-stale-claim",
                started_at=stale_now - timedelta(minutes=3),
            )
            assert started is True

        stale_list_result = await stale_list_tool.execute(
            ctx,
            stale_list_tool.parse_params(
                {"profile_key": "default"},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert stale_list_result.ok is True
        stale_claims = stale_list_result.payload["stale_task_claims"]
        assert isinstance(stale_claims, list)
        assert stale_claims[0]["task"]["id"] == task_id
        assert stale_claims[0]["claimed_by"] == "taskflow-runtime:stale-tool"

        maintenance_result = await maintenance_tool.execute(
            ctx,
            maintenance_tool.parse_params(
                {"profile_key": "default", "limit": 5},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert maintenance_result.ok is True
        maintenance = maintenance_result.payload["maintenance"]
        assert isinstance(maintenance, dict)
        assert maintenance["profile_id"] == "default"
        assert maintenance["repaired_count"] == 1
        assert maintenance["remaining_count"] == 0
        assert maintenance["remaining"] == []

        stale_list_after = await stale_list_tool.execute(
            ctx,
            stale_list_tool.parse_params(
                {"profile_key": "default"},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert stale_list_after.ok is True
        assert stale_list_after.payload["stale_task_claims"] == []

        event_list_result = await event_list_tool.execute(
            ctx,
            event_list_tool.parse_params(
                {"profile_key": "default", "task_id": task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert event_list_result.ok is True
        task_events = event_list_result.payload["task_events"]
        assert isinstance(task_events, list)
        assert {item["event_type"] for item in task_events} >= {"created", "lease_expired"}
    finally:
        await engine.dispose()


async def test_task_stale_plugins_support_structured_owner_filter(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Stale operator tools should scope inspection and repair to one structured executor owner."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="researcher",
            markdown="# Researcher\nFocus on stale research work.",
        )
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nFocus on stale review work.",
        )

        ctx = ToolContext(profile_id="default", session_id="s-task-maint-filtered", run_id=10)
        create_tool = registry.get("task.create")
        stale_list_tool = registry.get("task.stale.list")
        maintenance_tool = registry.get("task.stale.sweep")
        assert create_tool is not None
        assert stale_list_tool is not None
        assert maintenance_tool is not None

        reviewer_task = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Recover reviewer stale claim",
                    "description": "Repair only the reviewer stale claim.",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        researcher_task = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Leave researcher stale claim",
                    "description": "Keep the researcher stale claim untouched.",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "researcher",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert reviewer_task.ok is True
        assert researcher_task.ok is True

        reviewer_task_id = str(reviewer_task.payload["task"]["id"])
        researcher_task_id = str(researcher_task.payload["task"]["id"])
        stale_now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = TaskFlowRepository(session)
            for task_id, owner_ref, claim_token, worker_id in (
                (
                    reviewer_task_id,
                    "papercliper:reviewer",
                    "tool-filter-reviewer-claim",
                    "taskflow-runtime:reviewer",
                ),
                (
                    researcher_task_id,
                    "papercliper:researcher",
                    "tool-filter-researcher-claim",
                    "taskflow-runtime:researcher",
                ),
            ):
                claimed = await repo.claim_next_runnable_task(
                    now_utc=stale_now,
                    lease_until=stale_now - timedelta(minutes=2),
                    claim_token=claim_token,
                    claimed_by=worker_id,
                    profile_id="default",
                    owner_ref=owner_ref,
                )
                assert claimed is not None
                task_run = await repo.create_task_run(
                    task_id=task_id,
                    attempt=claimed.current_attempt,
                    owner_type=claimed.owner_type,
                    owner_ref=claimed.owner_ref,
                    execution_mode="detached",
                    status="running",
                    session_id=f"taskflow:{task_id}",
                    run_id=None,
                    worker_id=worker_id,
                    started_at=stale_now - timedelta(minutes=3),
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
                    started_at=stale_now - timedelta(minutes=3),
                )
                assert started is True

        stale_list_result = await stale_list_tool.execute(
            ctx,
            stale_list_tool.parse_params(
                {
                    "profile_key": "default",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert stale_list_result.ok is True
        stale_claims = stale_list_result.payload["stale_task_claims"]
        assert isinstance(stale_claims, list)
        assert [item["task"]["id"] for item in stale_claims] == [reviewer_task_id]

        maintenance_result = await maintenance_tool.execute(
            ctx,
            maintenance_tool.parse_params(
                {
                    "profile_key": "default",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                    "limit": 5,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert maintenance_result.ok is True
        maintenance = maintenance_result.payload["maintenance"]
        assert isinstance(maintenance, dict)
        assert maintenance["owner_ref"] == "papercliper:reviewer"
        assert maintenance["repaired_count"] == 1
        assert maintenance["remaining_count"] == 0

        stale_list_after = await stale_list_tool.execute(
            ctx,
            stale_list_tool.parse_params(
                {"profile_key": "default"},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert stale_list_after.ok is True
        remaining_claims = stale_list_after.payload["stale_task_claims"]
        assert isinstance(remaining_claims, list)
        assert [item["task"]["id"] for item in remaining_claims] == [researcher_task_id]
    finally:
        await engine.dispose()


async def test_task_review_plugins_handle_inbox_and_review_actions(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Review plugins should list inbox items and drive explicit review transitions."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        ctx = ToolContext(profile_id="default", session_id="s-review", run_id=2)

        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        get_tool = registry.get("task.get")
        review_list_tool = registry.get("task.review.list")
        review_approve_tool = registry.get("task.review.approve")
        review_request_changes_tool = registry.get("task.review.request_changes")
        assert create_tool is not None
        assert update_tool is not None
        assert get_tool is not None
        assert review_list_tool is not None
        assert review_approve_tool is not None
        assert review_request_changes_tool is not None

        review_task_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Review AI answer",
                    "description": "Review the AI-produced answer.",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                    "reviewer_type": "ai_profile",
                    "reviewer_ref": "default",
                    "labels": ["review"],
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert review_task_result.ok is True
        review_task = review_task_result.payload["task"]
        assert isinstance(review_task, dict)
        review_task_id = str(review_task["id"])

        mark_review_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": review_task_id,
                    "status": "review",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert mark_review_result.ok is True

        dependent_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Publish after review",
                    "description": "Publish after review is approved.",
                    "depends_on_task_ids": [review_task_id],
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert dependent_result.ok is True
        dependent_task = dependent_result.payload["task"]
        assert isinstance(dependent_task, dict)
        dependent_task_id = str(dependent_task["id"])

        review_list_result = await review_list_tool.execute(
            ctx,
            review_list_tool.parse_params(
                {
                    "profile_key": "default",
                    "actor_type": "ai_profile",
                    "actor_ref": "default",
                    "labels": ["review"],
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert review_list_result.ok is True
        inbox = review_list_result.payload["review_tasks"]
        assert isinstance(inbox, list)
        assert inbox[0]["id"] == review_task_id

        review_approve_result = await review_approve_tool.execute(
            ctx,
            review_approve_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": review_task_id,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert review_approve_result.ok is True
        approved_task = review_approve_result.payload["task"]
        assert isinstance(approved_task, dict)
        assert approved_task["status"] == "completed"

        dependent_get_result = await get_tool.execute(
            ctx,
            get_tool.parse_params(
                {"profile_key": "default", "task_id": dependent_task_id},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert dependent_get_result.ok is True
        unblocked_dependent = dependent_get_result.payload["task"]
        assert isinstance(unblocked_dependent, dict)
        assert unblocked_dependent["status"] == "todo"

        change_task_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "default",
                    "title": "Return for changes",
                    "description": "Send this task back with review feedback.",
                    "owner_type": "human",
                    "owner_ref": "cli_user:alice",
                    "reviewer_type": "ai_profile",
                    "reviewer_ref": "default",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert change_task_result.ok is True
        change_task = change_task_result.payload["task"]
        assert isinstance(change_task, dict)
        change_task_id = str(change_task["id"])

        change_mark_review_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": change_task_id,
                    "status": "review",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert change_mark_review_result.ok is True

        request_changes_result = await review_request_changes_tool.execute(
            ctx,
            review_request_changes_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": change_task_id,
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                    "reason_text": "Add citations before approval.",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert request_changes_result.ok is True
        changed_task = request_changes_result.payload["task"]
        assert isinstance(changed_task, dict)
        assert changed_task["status"] == "blocked"
        assert changed_task["owner_type"] == "ai_profile"
        assert changed_task["owner_ref"] == "default"
        assert changed_task["blocked_reason_code"] == "review_changes_requested"

        spoofed_approve_result = await review_approve_tool.execute(
            ctx,
            review_approve_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": review_task_id,
                    "actor_type": "human",
                    "actor_ref": "cli_user:alice",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert spoofed_approve_result.ok is False
        assert spoofed_approve_result.error_code == "task_review_actor_forbidden"

        spoofed_change_result = await review_request_changes_tool.execute(
            ctx,
            review_request_changes_tool.parse_params(
                {
                    "profile_key": "default",
                    "task_id": change_task_id,
                    "actor_type": "human",
                    "actor_ref": "cli_user:alice",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                    "reason_text": "This spoofed actor should be rejected.",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert spoofed_change_result.ok is False
        assert spoofed_change_result.error_code == "task_review_actor_forbidden"
    finally:
        await engine.dispose()


async def test_task_review_request_changes_supports_structured_ai_subagent_owner(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.review.request_changes should accept structured ai_subagent owner reassignment."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-only subagent.",
        )

        ctx = ToolContext(profile_id="default", session_id="s-review", run_id=5)
        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        review_request_changes_tool = registry.get("task.review.request_changes")
        assert create_tool is not None
        assert update_tool is not None
        assert review_request_changes_tool is not None

        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Structured changes request",
                    "description": "Return this review task to one subagent.",
                    "owner_type": "human",
                    "owner_ref": "cli_user:alice",
                    "reviewer_type": "ai_profile",
                    "reviewer_ref": "default",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task_id = str(create_result.payload["task"]["id"])

        review_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "task_id": task_id,
                    "status": "review",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert review_result.ok is True

        request_changes_result = await review_request_changes_tool.execute(
            ctx,
            review_request_changes_tool.parse_params(
                {
                    "task_id": task_id,
                    "reason_text": "Route this back to the reviewer specialist.",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )

        assert request_changes_result.ok is True
        task_payload = request_changes_result.payload["task"]
        assert task_payload["status"] == "blocked"
        assert task_payload["owner_type"] == "ai_subagent"
        assert task_payload["owner_ref"] == "papercliper:reviewer"
    finally:
        await engine.dispose()


async def test_task_review_tools_accept_subagent_actor_alias_for_current_runtime(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Review action tools should treat actor_type=subagent as the current ai_subagent actor."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\nReview-only subagent.",
        )
        service = get_task_flow_service(settings)
        approval_task = await service.create_task(
            profile_id="default",
            title="Approve as subagent alias",
            description="Approve this task from the detached reviewer runtime.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            reviewer_type="ai_subagent",
            reviewer_ref="papercliper:reviewer",
        )
        await service.update_task(profile_id="default", task_id=approval_task.id, status="review")
        changes_task = await service.create_task(
            profile_id="default",
            title="Request changes as subagent alias",
            description="Request changes from the detached reviewer runtime.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="human",
            owner_ref="cli_user:alice",
            reviewer_type="ai_subagent",
            reviewer_ref="papercliper:reviewer",
        )
        await service.update_task(profile_id="default", task_id=changes_task.id, status="review")

        runtime_context = {
            "taskflow_detached_runtime": {
                "owner_type": "ai_subagent",
                "owner_ref": "papercliper:reviewer",
            },
        }
        ctx = ToolContext(
            profile_id="papercliper",
            session_id="taskflow:reviewer-alias",
            run_id=6,
            runtime_metadata={
                "taskflow": {
                    "task_id": approval_task.id,
                    "task_profile_id": "default",
                },
            },
            trusted_runtime_context=runtime_context,
        )
        review_approve_tool = registry.get("task.review.approve")
        review_request_changes_tool = registry.get("task.review.request_changes")
        assert review_approve_tool is not None
        assert review_request_changes_tool is not None

        approve_result = await review_approve_tool.execute(
            ctx,
            review_approve_tool.parse_params(
                {
                    "task_id": approval_task.id,
                    "actor_type": "subagent",
                    "actor_ref": "papercliper:reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert approve_result.ok is True
        assert approve_result.payload["task"]["status"] == "completed"

        request_changes_result = await review_request_changes_tool.execute(
            ctx,
            review_request_changes_tool.parse_params(
                {
                    "task_id": changes_task.id,
                    "actor_type": "subagent",
                    "actor_ref": "papercliper:reviewer",
                    "owner_type": "ai_profile",
                    "owner_ref": "default",
                    "reason_text": "Add the missing details.",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert request_changes_result.ok is True
        changed_task = request_changes_result.payload["task"]
        assert changed_task["status"] == "blocked"
        assert changed_task["owner_type"] == "ai_profile"
        assert changed_task["owner_ref"] == "default"
    finally:
        await engine.dispose()


async def test_task_review_list_supports_structured_ai_subagent_actor(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.review.list should accept structured actor selectors for ai_subagent inboxes."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\n\nReview specialist.",
        )
        ctx = ToolContext(profile_id="papercliper", session_id="s-review-structured", run_id=2)

        create_tool = registry.get("task.create")
        update_tool = registry.get("task.update")
        review_list_tool = registry.get("task.review.list")
        assert create_tool is not None
        assert update_tool is not None
        assert review_list_tool is not None

        task_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "papercliper",
                    "title": "Structured review inbox",
                    "description": "List review work for one structured subagent actor.",
                    "owner_profile_id": "papercliper",
                    "reviewer_profile_id": "papercliper",
                    "reviewer_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert task_result.ok is True
        task = task_result.payload["task"]
        assert isinstance(task, dict)

        mark_review_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "profile_key": "papercliper",
                    "task_id": str(task["id"]),
                    "status": "review",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert mark_review_result.ok is True

        review_list_result = await review_list_tool.execute(
            ctx,
            review_list_tool.parse_params(
                {
                    "profile_key": "papercliper",
                    "actor_profile_id": "papercliper",
                    "actor_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert review_list_result.ok is True
        review_tasks = review_list_result.payload["review_tasks"]
        assert isinstance(review_tasks, list)
        assert [item["id"] for item in review_tasks] == [task["id"]]
    finally:
        await engine.dispose()


async def test_task_review_list_rejects_explicit_human_actor_with_structured_ai_selector(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """task.review.list should fail closed when human actor_type conflicts with structured AI actor fields."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\n\nReview specialist.",
        )
        ctx = ToolContext(profile_id="papercliper", session_id="s-review-structured-invalid", run_id=2)
        review_list_tool = registry.get("task.review.list")
        assert review_list_tool is not None

        review_list_result = await review_list_tool.execute(
            ctx,
            review_list_tool.parse_params(
                {
                    "profile_key": "papercliper",
                    "actor_type": "human",
                    "actor_profile_id": "papercliper",
                    "actor_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert review_list_result.ok is False
        assert review_list_result.error_code == "invalid_owner_type"
    finally:
        await engine.dispose()


async def test_task_list_and_board_support_structured_ai_owner_filters(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Read-only task views should accept structured owner selectors without manual owner_ref strings."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("papercliper")
        _write_profile_subagent(
            settings=settings,
            profile_id="papercliper",
            subagent_name="reviewer",
            markdown="# Reviewer\n\nReview specialist.",
        )

        ctx = ToolContext(profile_id="papercliper", session_id="s-list-board-structured", run_id=3)
        create_tool = registry.get("task.create")
        list_tool = registry.get("task.list")
        board_tool = registry.get("task.board")
        assert create_tool is not None
        assert list_tool is not None
        assert board_tool is not None

        reviewer_task_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "papercliper",
                    "title": "Review task",
                    "description": "Owned by one structured reviewer subagent.",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                    "labels": ["queue"],
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert reviewer_task_result.ok is True
        reviewer_task = reviewer_task_result.payload["task"]
        assert isinstance(reviewer_task, dict)

        orchestrator_task_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "profile_key": "papercliper",
                    "title": "Orchestrator task",
                    "description": "Owned by the orchestrator profile directly.",
                    "owner_profile_id": "papercliper",
                    "labels": ["queue"],
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert orchestrator_task_result.ok is True

        list_result = await list_tool.execute(
            ctx,
            list_tool.parse_params(
                {
                    "profile_key": "papercliper",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert list_result.ok is True
        listed_tasks = list_result.payload["tasks"]
        assert isinstance(listed_tasks, list)
        assert [item["id"] for item in listed_tasks] == [reviewer_task["id"]]

        board_result = await board_tool.execute(
            ctx,
            board_tool.parse_params(
                {
                    "profile_key": "papercliper",
                    "owner_profile_id": "papercliper",
                    "owner_subagent_name": "reviewer",
                    "labels": ["queue"],
                    "limit_per_column": 5,
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert board_result.ok is True
        board = board_result.payload["board"]
        assert isinstance(board, dict)
        assert board["total_count"] == 1
        todo_column = next(column for column in board["columns"] if column["id"] == "todo")
        assert todo_column["count"] == 1
        assert [item["id"] for item in todo_column["tasks"]] == [reviewer_task["id"]]
    finally:
        await engine.dispose()
