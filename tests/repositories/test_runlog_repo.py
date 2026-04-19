"""Tests for runlog repository read path and run repository latest-id helper."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.session import session_scope
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from tests.repositories._harness import build_repository_factory


async def _prepare(tmp_path: Path) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    return await build_repository_factory(
        tmp_path,
        db_name="runlog_repo.db",
        profile_ids=("default", "other"),
        session_specs=(
            ("s-1", "default"),
            ("s-2", "default"),
            ("s-3", "other"),
        ),
    )


async def test_runlog_repo_list_run_events_since_ordering_filters_after_id_limit(
    tmp_path: Path,
) -> None:
    """Repository should list only run events after cursor in ascending id order."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            main_run = await run_repo.create_run(session_id="s-1", profile_id="default")
            other_run = await run_repo.create_run(session_id="s-2", profile_id="default")

            first = await runlog_repo.create_event(
                run_id=main_run.id,
                session_id="s-1",
                event_type="turn.progress",
                payload={"step": 1},
            )
            second = await runlog_repo.create_event(
                run_id=main_run.id,
                session_id="s-1",
                event_type="turn.progress",
                payload={"step": 2},
            )
            await runlog_repo.create_event(
                run_id=other_run.id,
                session_id="s-2",
                event_type="turn.progress",
                payload={"step": 99},
            )
            third = await runlog_repo.create_event(
                run_id=main_run.id,
                session_id="s-1",
                event_type="turn.finalize",
                payload={"step": 3},
            )

            all_events = await runlog_repo.list_run_events_since(run_id=main_run.id)
            assert [event.id for event in all_events] == [first.id, second.id, third.id]
            assert [event.run_id for event in all_events] == [main_run.id, main_run.id, main_run.id]
            assert all_events[0].payload_json == '{"step": 1}'
            assert all_events[0].created_at is not None

            filtered = await runlog_repo.list_run_events_since(
                run_id=main_run.id,
                after_event_id=second.id,
            )
            assert [event.id for event in filtered] == [third.id]

            limited = await runlog_repo.list_run_events_since(run_id=main_run.id, limit=2)
            assert [event.id for event in limited] == [first.id, second.id]
    finally:
        await engine.dispose()


async def test_run_repo_get_latest_run_id(tmp_path: Path) -> None:
    """Repository should return latest run id by profile/session or None when missing."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = RunRepository(session)
            assert await repo.get_latest_run_id(profile_id="default", session_id="s-1") is None

            first = await repo.create_run(session_id="s-1", profile_id="default")
            second = await repo.create_run(session_id="s-2", profile_id="default")
            latest_for_s1 = await repo.create_run(session_id="s-1", profile_id="default")
            other_profile = await repo.create_run(session_id="s-3", profile_id="other")

            assert await repo.get_latest_run_id(profile_id="default", session_id="s-1") == latest_for_s1.id
            assert await repo.get_latest_run_id(profile_id="default", session_id="s-2") == second.id
            assert await repo.get_latest_run_id(profile_id="other", session_id="s-3") == other_profile.id
            assert await repo.get_latest_run_id(profile_id="other", session_id="s-1") is None
            assert await repo.get_latest_run_id(profile_id="default", session_id="missing") is None
            assert latest_for_s1.id > first.id
            assert (
                await repo.is_run_owned_by_session(
                    run_id=latest_for_s1.id,
                    profile_id="default",
                    session_id="s-1",
                )
                is True
            )
            assert (
                await repo.is_run_owned_by_session(
                    run_id=latest_for_s1.id,
                    profile_id="other",
                    session_id="s-1",
                )
                is False
            )
    finally:
        await engine.dispose()


async def test_run_repo_request_cancel_marks_latest_running_only(tmp_path: Path) -> None:
    """Cancellation should target only the newest running run for the session scope."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            repo = RunRepository(session)
            first = await repo.create_run(session_id="s-1", profile_id="default", status="running")
            latest = await repo.create_run(session_id="s-1", profile_id="default", status="running")
            await repo.create_run(session_id="s-1", profile_id="default", status="completed")
            await repo.create_run(session_id="s-2", profile_id="default", status="running")

            assert await repo.request_cancel(profile_id="default", session_id="s-1") is True
            assert await repo.is_cancel_requested(first.id) is False
            assert await repo.is_cancel_requested(latest.id) is True
    finally:
        await engine.dispose()


async def test_runlog_repo_list_session_events_filters_before_limit(tmp_path: Path) -> None:
    """Session event listing should filter by type and keep reverse-chronological order."""

    engine, factory = await _prepare(tmp_path)
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            run = await run_repo.create_run(session_id="s-1", profile_id="default")

            first = await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.progress",
                payload={"step": 1},
            )
            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.finalize",
                payload={"step": 2},
            )
            third = await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.progress",
                payload={"step": 3},
            )

            filtered = await runlog_repo.list_session_events(
                session_id="s-1",
                event_type="turn.progress",
                limit=2,
            )
            assert [event.id for event in filtered] == [third.id, first.id]
            assert [event.event_type for event in filtered] == ["turn.progress", "turn.progress"]
    finally:
        await engine.dispose()
