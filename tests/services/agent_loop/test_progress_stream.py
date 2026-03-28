"""Tests for progress stream polling and stage mapping."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.services.agent_loop import ProgressCursor, ProgressStream
from afkbot.settings import Settings


async def _prepare(
    tmp_path: Path,
    db_name: str,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / db_name}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        await ChatSessionRepository(session).create(session_id="s-1", profile_id="default")
    return engine, factory


async def test_progress_stream_cursor_monotonic_and_stage_mapping(tmp_path: Path) -> None:
    """Polling should return canonical stages and advance cursor monotonically."""

    engine, factory = await _prepare(tmp_path, "progress_stream_cursor.db")
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            run = await run_repo.create_run(session_id="s-1", profile_id="default", status="running")

            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.think",
                payload={},
            )
            planning_event = await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.progress",
                payload={"stage": "planning", "iteration": 1},
            )
            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="tool.call",
                payload={"name": "debug.echo", "params": {}},
            )
            finalize_event = await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.finalize",
                payload={},
            )

            stream = ProgressStream(session, batch_size=2)
            cursor = ProgressCursor()

            first_batch, first_cursor = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=cursor,
            )
            assert [event.stage for event in first_batch] == ["thinking", "planning"]
            assert first_batch[1].iteration == 1
            assert first_cursor.run_id == run.id
            assert first_cursor.last_event_id == planning_event.id

            second_batch, second_cursor = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=first_cursor,
            )
            assert [event.stage for event in second_batch] == ["tool_call", "done"]
            assert second_batch[0].tool_name == "debug.echo"
            assert second_cursor.run_id == run.id
            assert second_cursor.last_event_id == finalize_event.id
            assert second_cursor.last_event_id > first_cursor.last_event_id

            third_batch, third_cursor = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=second_cursor,
            )
            assert third_batch == []
            assert third_cursor == second_cursor
    finally:
        await engine.dispose()


async def test_progress_stream_hides_turn_plan_event_when_plan_mode_is_off(tmp_path: Path) -> None:
    """Non-plan turns should not surface `turn.plan` as public planning progress."""

    engine, factory = await _prepare(tmp_path, "progress_stream_turn_plan_hidden.db")
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            run = await run_repo.create_run(session_id="s-1", profile_id="default", status="running")

            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.plan",
                payload={"planning_mode": "off", "thinking_level": "high"},
            )
            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.progress",
                payload={"stage": "thinking", "iteration": 1},
            )

            stream = ProgressStream(session, batch_size=50)
            events, _ = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=ProgressCursor(),
            )

            assert [event.stage for event in events] == ["thinking"]
            assert [event.event_type for event in events] == ["turn.progress"]
    finally:
        await engine.dispose()


async def test_progress_stream_does_not_dedupe_events(tmp_path: Path) -> None:
    """Stream should return all source events, including repeated canonical stages."""

    engine, factory = await _prepare(tmp_path, "progress_stream_dedupe.db")
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            run = await run_repo.create_run(session_id="s-1", profile_id="default", status="running")

            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.progress",
                payload={"stage": "thinking", "iteration": 1},
            )
            second = await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="turn.progress",
                payload={"stage": "thinking", "iteration": 2},
            )

            stream = ProgressStream(session, batch_size=50)
            events, cursor = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=ProgressCursor(),
            )

            assert [event.stage for event in events] == ["thinking", "thinking"]
            assert [event.iteration for event in events] == [1, 2]
            assert cursor.last_event_id == second.id
    finally:
        await engine.dispose()


async def test_progress_stream_maps_finalize_cancel_and_subagent_wait(tmp_path: Path) -> None:
    """Stream should map terminal finalize/cancel and subagent wait tool stages."""

    engine, factory = await _prepare(tmp_path, "progress_stream_terminal.db")
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)

            first_run = await run_repo.create_run(
                session_id="s-1",
                profile_id="default",
                status="completed",
            )
            finalize_event = await runlog_repo.create_event(
                run_id=first_run.id,
                session_id="s-1",
                event_type="turn.finalize",
                payload={},
            )

            stream = ProgressStream(session, batch_size=50)
            first_batch, first_cursor = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=ProgressCursor(),
            )
            assert [event.stage for event in first_batch] == ["done"]
            assert first_cursor.run_id == first_run.id
            assert first_cursor.last_event_id == finalize_event.id

            second_run = await run_repo.create_run(
                session_id="s-1",
                profile_id="default",
                status="cancelled",
            )
            await runlog_repo.create_event(
                run_id=second_run.id,
                session_id="s-1",
                event_type="tool.call",
                payload={"name": "subagent.wait", "params": {}},
            )
            cancel_event = await runlog_repo.create_event(
                run_id=second_run.id,
                session_id="s-1",
                event_type="turn.cancel",
                payload={},
            )

            second_batch, second_cursor = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=ProgressCursor(),
            )
            assert [event.stage for event in second_batch] == ["subagent_wait", "cancelled"]
            assert second_batch[0].tool_name == "subagent.wait"
            assert second_cursor.run_id == second_run.id
            assert second_cursor.last_event_id == cancel_event.id
    finally:
        await engine.dispose()


async def test_progress_stream_maps_llm_call_events_to_thinking(tmp_path: Path) -> None:
    """LLM call lifecycle events should be exposed as thinking-stage progress."""

    engine, factory = await _prepare(tmp_path, "progress_stream_llm_call.db")
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            run = await run_repo.create_run(session_id="s-1", profile_id="default", status="running")

            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="llm.call.start",
                payload={"iteration": 1, "timeout_ms": 30000},
            )
            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="llm.call.tick",
                payload={"iteration": 1, "elapsed_ms": 3000, "timeout_ms": 30000},
            )
            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="llm.call.done",
                payload={"iteration": 1, "elapsed_ms": 3500, "response_kind": "tool_calls"},
            )

            stream = ProgressStream(session, batch_size=50)
            events, _cursor = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=ProgressCursor(),
            )

            assert [event.stage for event in events] == ["thinking", "thinking", "thinking"]
            assert [event.event_type for event in events] == [
                "llm.call.start",
                "llm.call.tick",
                "llm.call.done",
            ]
            assert events[1].iteration == 1
    finally:
        await engine.dispose()


async def test_progress_stream_rejects_foreign_cursor_run_id(tmp_path: Path) -> None:
    """Stream should not read events when cursor.run_id belongs to another profile/session."""

    engine, factory = await _prepare(tmp_path, "progress_stream_foreign_cursor.db")
    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("other")
            await ChatSessionRepository(session).create(session_id="s-foreign", profile_id="other")
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            foreign_run = await run_repo.create_run(
                session_id="s-foreign",
                profile_id="other",
                status="running",
            )
            await runlog_repo.create_event(
                run_id=foreign_run.id,
                session_id="s-foreign",
                event_type="turn.progress",
                payload={"stage": "thinking", "iteration": 1},
            )
            stream = ProgressStream(session, batch_size=50)
            events, cursor = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=ProgressCursor(run_id=foreign_run.id),
            )
            assert events == []
            assert cursor.run_id == foreign_run.id
            assert cursor.last_event_id == 0
    finally:
        await engine.dispose()


async def test_progress_stream_attaches_tool_call_and_result_details(tmp_path: Path) -> None:
    """Progress events should carry non-serialized tool details for CLI rendering."""

    engine, factory = await _prepare(tmp_path, "progress_stream_tool_details.db")
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            run = await run_repo.create_run(session_id="s-1", profile_id="default", status="running")

            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="tool.call",
                payload={
                    "name": "bash.exec",
                    "params": {"cmd": "pwd", "cwd": ".", "timeout_sec": 15},
                },
            )
            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="tool.result",
                payload={
                    "name": "bash.exec",
                    "result": {"ok": True, "payload": {"exit_code": 0, "stdout": "/repo"}},
                },
            )

            stream = ProgressStream(session, batch_size=50)
            events, _ = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=ProgressCursor(),
            )
            assert len(events) == 2
            assert events[0].payload == {
                "name": "bash.exec",
                "params": {"cmd": "pwd", "cwd": ".", "timeout_sec": 15},
            }
            assert events[0].tool_call_params == {"cmd": "pwd", "cwd": ".", "timeout_sec": 15}
            assert events[0].tool_result is None
            assert events[1].tool_call_params is None
            assert events[1].payload == {
                "name": "bash.exec",
                "result": {"ok": True, "payload": {"exit_code": 0, "stdout": "/repo"}},
            }
            assert events[1].tool_result == {"ok": True, "payload": {"exit_code": 0, "stdout": "/repo"}}
    finally:
        await engine.dispose()


async def test_progress_stream_attaches_tool_progress_details(tmp_path: Path) -> None:
    """Progress stream should carry tool.progress payloads for live CLI rendering."""

    # Arrange
    engine, factory = await _prepare(tmp_path, "progress_stream_tool_progress.db")
    try:
        async with session_scope(factory) as session:
            run_repo = RunRepository(session)
            runlog_repo = RunlogRepository(session)
            run = await run_repo.create_run(session_id="s-1", profile_id="default", status="running")

            await runlog_repo.create_event(
                run_id=run.id,
                session_id="s-1",
                event_type="tool.progress",
                payload={
                    "name": "bash.exec",
                    "progress": {
                        "preview_lines": ["stdout | one", "stdout | two"],
                        "stream": "mixed",
                    },
                },
            )
            stream = ProgressStream(session, batch_size=50)

            # Act
            events, _ = await stream.poll(
                profile_id="default",
                session_id="s-1",
                cursor=ProgressCursor(),
            )

            # Assert
            assert len(events) == 1
            assert events[0].stage == "tool_call"
            assert events[0].tool_name == "bash.exec"
            assert events[0].tool_call_params is None
            assert events[0].tool_result is None
            assert events[0].tool_progress == {
                "preview_lines": ["stdout | one", "stdout | two"],
                "stream": "mixed",
            }
    finally:
        await engine.dispose()
