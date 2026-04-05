"""Integration tests for Task Flow tool plugins."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository
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

    return settings, engine, ToolRegistry.from_settings(settings)


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
        assert update_tool is not None
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
