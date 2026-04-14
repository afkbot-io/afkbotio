"""Integration tests for Task Flow tool plugins."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
from afkbot.services.profile_runtime import ProfileRuntimeConfig, get_profile_runtime_config_service
from afkbot.services.task_flow import reset_task_flow_services
from afkbot.services.tools.base import ToolContext
from afkbot.services.tools.registry import ToolRegistry
from afkbot.settings import Settings, get_settings


async def _prepare(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> tuple[Settings, AsyncEngine, ToolRegistry]:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'tools_taskflow.db'}"
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", db_url)
    get_settings.cache_clear()
    reset_task_flow_services()

    settings = get_settings()
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        sessions = ChatSessionRepository(session)
        for session_id in (
            "s-task",
            "session-live-42",
            "task-seed",
            "s-maint",
            "s-task-maint",
            "s-review",
        ):
            await sessions.create(session_id=session_id, profile_id="default")
        await ProfileRepository(session).get_or_create_default("analyst")
        for session_id in ("taskflow:task_demo", "taskflow:analyst-demo"):
            await sessions.create(session_id=session_id, profile_id="analyst")

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
) -> None:
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default(profile_id)
        await ChatSessionRepository(session).create(session_id=session_id, profile_id=profile_id)


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
                "prompt": "Compile the weekly report and store the summary.",
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
                    "prompt": "Collect the source data before preparing the report.",
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
                    "prompt": "Attach the current session when work starts.",
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
                    "prompt": "Recheck the external vendor later.",
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
                    "prompt": "Attach a delegated execution session.",
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
                    "prompt": "Work this task until the vendor answers.",
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
                    "prompt": "Pause until the delegated teammate finishes.",
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
                    "prompt": "Repair a stale claim via explicit maintenance.",
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


async def test_task_plugins_allow_taskflow_runtime_to_manage_backlog_profile(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Task tools should target backlog profile inside taskflow runtime even under another AI profile."""

    settings, engine, registry = await _prepare(tmp_path, monkeypatch)
    try:
        factory = create_session_factory(engine)
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("analyst")
        _write_team_runtime_config(
            settings=settings,
            profile_id="default",
            team_profile_ids=("analyst",),
        )

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
        assert create_tool is not None
        create_result = await create_tool.execute(
            ctx,
            create_tool.parse_params(
                {
                    "title": "Prepare handoff notes",
                    "prompt": "Prepare concise handoff notes for the human reviewer.",
                    "owner_type": "ai_profile",
                    "owner_ref": "analyst",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert create_result.ok is True
        task = create_result.payload["task"]
        assert isinstance(task, dict)
        assert task["profile_id"] == "default"
        task_id = str(task["id"])

        update_tool = registry.get("task.update")
        assert update_tool is not None
        update_result = await update_tool.execute(
            ctx,
            update_tool.parse_params(
                {
                    "task_id": task_id,
                    "owner_type": "human",
                    "owner_ref": "cli_user:alice",
                    "status": "review",
                    "blocked_reason_code": "awaiting_human_review",
                    "blocked_reason_text": "Ready for human review.",
                },
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert update_result.ok is True
        updated = update_result.payload["task"]
        assert isinstance(updated, dict)
        assert updated["profile_id"] == "default"
        assert updated["owner_type"] == "human"
        assert updated["owner_ref"] == "cli_user:alice"
        assert updated["status"] == "review"

        list_tool = registry.get("task.list")
        assert list_tool is not None
        list_result = await list_tool.execute(
            ctx,
            list_tool.parse_params(
                {"owner_type": "human", "owner_ref": "cli_user:alice"},
                default_timeout_sec=settings.tool_timeout_default_sec,
                max_timeout_sec=settings.tool_timeout_max_sec,
            ),
        )
        assert list_result.ok is True
        listed = list_result.payload["tasks"]
        assert isinstance(listed, list)
        assert listed[0]["id"] == task_id
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
                    "prompt": "Take over the research-heavy follow-up work.",
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
                    "prompt": "Prepare the launch brief and delegate research.",
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
                    "prompt": "Research competitor pricing and summarize the deltas.",
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
                    "prompt": "This task belongs to Papercliper.",
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
                    "prompt": "Repair the stale claim and retry the task.",
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
                    "prompt": "Review the AI-produced answer.",
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
                    "prompt": "Publish after review is approved.",
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
                    "prompt": "Send this task back with review feedback.",
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
