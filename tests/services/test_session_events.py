"""Contract tests for append-heavy session event stores."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.runlog_repo import RunlogRepository
from afkbot.services.session_events import (
    DatabaseRunlogEventStore,
    DualRunlogEventStore,
    JsonlRunlogEventStore,
)
from afkbot.settings import Settings


async def test_jsonl_runlog_event_store_appends_and_reads_run_events(tmp_path: Path) -> None:
    """JSONL runlog storage should preserve run cursors without touching SQLite."""

    store = JsonlRunlogEventStore(root_dir=tmp_path)

    first = await store.create_event(
        run_id=10,
        session_id="session-a",
        event_type="turn.progress",
        payload={"stage": "thinking", "iteration": 0},
    )
    second = await store.create_event(
        run_id=10,
        session_id="session-a",
        event_type="tool.result",
        payload={"name": "task.create", "result": {"ok": True}},
    )
    await store.create_event(
        run_id=11,
        session_id="session-a",
        event_type="turn.progress",
        payload={"stage": "done"},
    )

    rows = await store.list_run_events_since(run_id=10, after_event_id=first.id, limit=10)
    recent_results = await store.list_session_events(
        session_id="session-a",
        event_type="tool.result",
        limit=5,
    )

    assert [row.id for row in rows] == [second.id]
    assert recent_results[0].event_type == "tool.result"
    assert json.loads(recent_results[0].payload_json)["name"] == "task.create"


async def test_jsonl_runlog_event_store_skips_corrupt_partial_lines(tmp_path: Path) -> None:
    """Reader recovery should tolerate a torn final JSONL line after a crash."""

    store = JsonlRunlogEventStore(root_dir=tmp_path)
    await store.create_event(
        run_id=20,
        session_id="session-b",
        event_type="turn.progress",
        payload={"stage": "thinking"},
    )
    event_path = next((tmp_path / "session_events").glob("*/*.jsonl"))
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write('{"id":')

    rows = await store.list_run_events_since(run_id=20, after_event_id=0, limit=10)

    assert len(rows) == 1
    assert rows[0].event_type == "turn.progress"


async def test_jsonl_runlog_event_store_uses_run_index_instead_of_global_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run reads should resolve run->session index and avoid scanning unrelated files."""

    store = JsonlRunlogEventStore(root_dir=tmp_path)
    await store.create_event(
        run_id=30,
        session_id="target-session",
        event_type="turn.progress",
        payload={"stage": "thinking"},
    )
    for index in range(8):
        await store.create_event(
            run_id=100 + index,
            session_id=f"other-session-{index}",
            event_type="turn.progress",
            payload={"stage": "thinking"},
        )

    def _raise_global_scan() -> list[object]:
        raise AssertionError("global scan should not be used when run index exists")

    monkeypatch.setattr(store, "_read_all_events_sync", _raise_global_scan)

    rows = await store.list_run_events_since(run_id=30, after_event_id=0, limit=10)
    assert [row.session_id for row in rows] == ["target-session"]


async def test_jsonl_runlog_event_store_defers_append_until_db_commit(tmp_path: Path) -> None:
    """JSONL event lines should be written after successful DB commit, not before."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'events_defer.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="session-defer",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            assert await ProfileRepository(session).get("default") is not None
            store = JsonlRunlogEventStore(root_dir=tmp_path, session=session)
            row = await store.create_event(
                run_id=40,
                session_id="session-defer",
                event_type="turn.progress",
                payload={"stage": "thinking"},
            )
            assert row.id == 1
            assert list((tmp_path / "session_events").glob("*/*.jsonl")) == []
        assert list((tmp_path / "session_events").glob("*/*.jsonl"))

        async with session_scope(factory) as session:
            assert await ProfileRepository(session).get("default") is not None
            store = JsonlRunlogEventStore(root_dir=tmp_path, session=session)
            await store.create_event(
                run_id=41,
                session_id="session-rollback",
                event_type="turn.progress",
                payload={"stage": "thinking"},
            )
            await session.rollback()
        rows = await JsonlRunlogEventStore(root_dir=tmp_path).list_run_events_since(
            run_id=41,
            after_event_id=0,
            limit=10,
        )
        assert rows == []
    finally:
        await engine.dispose()


async def test_dual_runlog_event_store_create_rolls_back_without_jsonl_leak(
    tmp_path: Path,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'events_dual_rollback.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="session-dual-rollback",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            run = await RunRepository(session).create_run(
                session_id="session-dual-rollback",
                profile_id="default",
                status="running",
            )
            store = DualRunlogEventStore(
                primary=DatabaseRunlogEventStore(RunlogRepository(session)),
                mirror=JsonlRunlogEventStore(root_dir=tmp_path, session=session),
            )
            await store.create_event(
                run_id=run.id,
                session_id="session-dual-rollback",
                event_type="turn.progress",
                payload={"stage": "queued"},
            )
            await session.rollback()

        rows = await JsonlRunlogEventStore(root_dir=tmp_path).list_run_events_since(
            run_id=1,
            after_event_id=0,
            limit=10,
        )
        assert rows == []
    finally:
        await engine.dispose()


async def test_jsonl_runlog_event_store_repairs_failed_post_commit_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Committed DB outbox rows should replay when the after-commit JSONL append fails."""

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'events_repair.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="session-repair",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            store = JsonlRunlogEventStore(root_dir=tmp_path, session=session)

            def _raise_apply(_mutation: dict[str, object]) -> None:
                raise OSError("simulated append failure")

            monkeypatch.setattr(store, "_apply_mutation_sync", _raise_apply)
            await store.create_event(
                run_id=50,
                session_id="session-repair",
                event_type="turn.progress",
                payload={"stage": "queued"},
            )

        assert list((tmp_path / "session_events").glob("*/*.jsonl")) == []

        async with session_scope(factory) as session:
            store = JsonlRunlogEventStore(root_dir=tmp_path, session=session)
            rows = await store.list_run_events_since(run_id=50, after_event_id=0, limit=10)

        assert [row.event_type for row in rows] == ["turn.progress"]
        assert list((tmp_path / "session_events").glob("*/*.jsonl"))
    finally:
        await engine.dispose()


async def test_jsonl_runlog_event_store_replays_all_committed_outbox_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'events_repair_many.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="session-repair-many",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            store = JsonlRunlogEventStore(root_dir=tmp_path, session=session)

            def _raise_apply(_mutation: dict[str, object]) -> None:
                raise OSError("simulated append failure")

            monkeypatch.setattr(store, "_apply_mutation_sync", _raise_apply)
            for index in range(105):
                await store.create_event(
                    run_id=55,
                    session_id="session-repair-many",
                    event_type="turn.progress",
                    payload={"index": index},
                )

        assert list((tmp_path / "session_events").glob("*/*.jsonl")) == []

        async with session_scope(factory) as session:
            rows = await JsonlRunlogEventStore(
                root_dir=tmp_path,
                session=session,
            ).list_run_events_since(run_id=55, after_event_id=0, limit=200)

        assert len(rows) == 105
        assert json.loads(rows[-1].payload_json)["index"] == 104
    finally:
        await engine.dispose()


async def test_jsonl_runlog_event_store_does_not_materialize_uncommitted_outbox_from_peer_store(
    tmp_path: Path,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'events_peer.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="session-peer",
                profile_id="default",
            )

        async with session_scope(factory) as session:
            writer = JsonlRunlogEventStore(root_dir=tmp_path, session=session)
            reader = JsonlRunlogEventStore(root_dir=tmp_path, session=session)
            await writer.create_event(
                run_id=60,
                session_id="session-peer",
                event_type="turn.progress",
                payload={"stage": "queued"},
            )

            rows = await reader.list_run_events_since(run_id=60, after_event_id=0, limit=10)

            assert rows == []
            assert list((tmp_path / "session_events").glob("*/*.jsonl")) == []

        rows = await JsonlRunlogEventStore(root_dir=tmp_path).list_run_events_since(
            run_id=60,
            after_event_id=0,
            limit=10,
        )
        assert [row.event_type for row in rows] == ["turn.progress"]
    finally:
        await engine.dispose()


async def test_jsonl_runlog_event_store_continues_legacy_file_sequence_with_db_outbox(
    tmp_path: Path,
) -> None:
    legacy_store = JsonlRunlogEventStore(root_dir=tmp_path)
    await legacy_store.create_event(
        run_id=70,
        session_id="session-legacy-jsonl",
        event_type="turn.progress",
        payload={"stage": "thinking", "iteration": 1},
    )
    await legacy_store.create_event(
        run_id=70,
        session_id="session-legacy-jsonl",
        event_type="turn.progress",
        payload={"stage": "thinking", "iteration": 2},
    )

    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'events_legacy_jsonl.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="session-legacy-jsonl",
                profile_id="default",
            )
            store = JsonlRunlogEventStore(root_dir=tmp_path, session=session)
            row = await store.create_event(
                run_id=70,
                session_id="session-legacy-jsonl",
                event_type="turn.progress",
                payload={"stage": "done", "iteration": 3},
            )

        rows = await JsonlRunlogEventStore(root_dir=tmp_path).list_run_events_since(
            run_id=70,
            after_event_id=0,
            limit=10,
        )
        assert row.id == 3
        assert [item.id for item in rows] == [1, 2, 3]
    finally:
        await engine.dispose()


async def test_jsonl_runlog_event_store_backfills_database_session_events_by_type(
    tmp_path: Path,
) -> None:
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'events_db_backfill.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="session-browser-backfill",
                profile_id="default",
            )
            run = await RunRepository(session).create_run(
                session_id="session-browser-backfill",
                profile_id="default",
            )
            await RunlogRepository(session).create_event(
                run_id=run.id,
                session_id="session-browser-backfill",
                event_type="browser.control",
                payload={"result": {"ok": True}},
            )

        async with session_scope(factory) as session:
            store = JsonlRunlogEventStore(root_dir=tmp_path, session=session)
            rows = await store.list_session_events_by_type(
                profile_id="default",
                session_id="session-browser-backfill",
                event_type="browser.control",
                limit=10,
            )

        assert len(rows) == 1
        assert rows[0].event_type == "browser.control"
        assert json.loads(rows[0].payload_json)["result"] == {"ok": True}
    finally:
        await engine.dispose()


async def test_jsonl_runlog_event_store_backfills_database_events_in_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import afkbot.services.session_events as session_events_module

    monkeypatch.setattr(session_events_module, "_DATABASE_BACKFILL_BATCH_SIZE", 2)
    settings = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path / 'events_db_batches.db'}")
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)

    try:
        async with session_scope(factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="session-batch-backfill",
                profile_id="default",
            )
            run = await RunRepository(session).create_run(
                session_id="session-batch-backfill",
                profile_id="default",
            )
            for index in range(3):
                await RunlogRepository(session).create_event(
                    run_id=run.id,
                    session_id="session-batch-backfill",
                    event_type="turn.progress",
                    payload={"index": index},
                )

        async with session_scope(factory) as session:
            store = JsonlRunlogEventStore(root_dir=tmp_path, session=session)
            rows = await store.list_run_events_since(
                run_id=run.id,
                after_event_id=0,
                limit=10,
            )

        assert [json.loads(row.payload_json)["index"] for row in rows] == [0, 1, 2]
    finally:
        await engine.dispose()
