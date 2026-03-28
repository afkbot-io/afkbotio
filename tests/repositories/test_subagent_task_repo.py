"""Tests for persisted subagent task repository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.subagent_task_repo import SubagentTaskRepository
from tests.repositories._harness import build_repository_factory


async def _prepare(
    tmp_path: Path,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    return await build_repository_factory(
        tmp_path,
        db_name="subagent_task_repo.db",
        profile_ids=("default",),
    )


async def test_repo_create_claim_finish(tmp_path: Path) -> None:
    """Repository should support core task state transitions."""

    engine, factory = await _prepare(tmp_path)
    try:
        task_id = "task-1"
        now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = SubagentTaskRepository(session)
            created = await repo.create_task(
                task_id=task_id,
                profile_id="default",
                session_id="s-1",
                run_id=1,
                subagent_name="researcher",
                prompt="hello",
                timeout_sec=10,
                created_at=now,
            )
            assert created.status == "running"

        async with session_scope(factory) as session:
            repo = SubagentTaskRepository(session)
            claimed = await repo.claim_task_start(task_id=task_id, started_at=now)
            assert claimed is not None
            assert claimed.started_at is not None

            second_claim = await repo.claim_task_start(task_id=task_id, started_at=now)
            assert second_claim is None

            finished = await repo.finish_task(
                task_id=task_id,
                status="completed",
                finished_at=now + timedelta(seconds=1),
                output="ok",
                error_code=None,
                reason=None,
            )
            assert finished is True

            final = await repo.get_task(task_id=task_id)
            assert final is not None
            assert final.status == "completed"
            assert final.output == "ok"
    finally:
        await engine.dispose()


async def test_repo_delete_finished_before(tmp_path: Path) -> None:
    """Repository should remove only finished rows older than threshold."""

    engine, factory = await _prepare(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        async with session_scope(factory) as session:
            repo = SubagentTaskRepository(session)
            await repo.create_task(
                task_id="old-finished",
                profile_id="default",
                session_id="s",
                run_id=1,
                subagent_name="researcher",
                prompt="x",
                timeout_sec=10,
                created_at=now - timedelta(minutes=10),
            )
            await repo.finish_task(
                task_id="old-finished",
                status="completed",
                finished_at=now - timedelta(minutes=5),
                output="x",
                error_code=None,
                reason=None,
            )

            await repo.create_task(
                task_id="new-finished",
                profile_id="default",
                session_id="s",
                run_id=1,
                subagent_name="researcher",
                prompt="y",
                timeout_sec=10,
                created_at=now - timedelta(minutes=2),
            )
            await repo.finish_task(
                task_id="new-finished",
                status="completed",
                finished_at=now - timedelta(seconds=30),
                output="y",
                error_code=None,
                reason=None,
            )

            deleted = await repo.delete_finished_before(before_utc=now - timedelta(minutes=1))
            assert deleted == 1

            assert await repo.get_task(task_id="old-finished") is None
            assert await repo.get_task(task_id="new-finished") is not None
    finally:
        await engine.dispose()
