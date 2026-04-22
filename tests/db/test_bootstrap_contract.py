"""Contract tests for the clean SQLite bootstrap baseline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import warnings

import pytest
from sqlalchemy import text

from afkbot.db.bootstrap import create_schema, list_applied_migrations, ping
from afkbot.db.bootstrap_runtime import prune_runtime_history
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.runlog_event import RunlogEvent
from afkbot.models.task_event import TaskEvent
from afkbot.models.task_run import TaskRun
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.settings import Settings
from afkbot.services.task_flow import TaskFlowServiceError
from afkbot.services.task_flow.service import TaskFlowService


async def test_create_schema_and_ping(tmp_path: Path) -> None:
    """Schema creation and ping should work for sqlite+aiosqlite."""

    # Arrange
    db_path = tmp_path / "test.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    # Act
    await create_schema(engine)
    reachable = await ping(engine)
    versions = await list_applied_migrations(engine)

    # Assert
    assert reachable is True
    assert versions == ()
    async with engine.connect() as conn:
        foreign_keys_enabled = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
        busy_timeout_ms = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
        journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
        synchronous_mode = (await conn.execute(text("PRAGMA synchronous"))).scalar_one()
    assert int(foreign_keys_enabled) == 1
    assert int(busy_timeout_ms) == 5000
    assert str(journal_mode).strip().lower() != ""
    if str(journal_mode).lower() == "wal":
        assert int(synchronous_mode) == 1
    await engine.dispose()


async def test_create_engine_registers_explicit_sqlite_datetime_adapters(tmp_path: Path) -> None:
    """SQLite engine setup should replace Python's deprecated default datetime adapter."""

    settings = Settings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'datetime-adapter.db'}",
        root_dir=tmp_path,
    )
    engine = create_engine(settings)

    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            adapted = sqlite3.adapt(datetime.now(timezone.utc))
        assert isinstance(adapted, str)
        assert not [warning for warning in caught if issubclass(warning.category, DeprecationWarning)]
    finally:
        await engine.dispose()


async def test_create_schema_is_idempotent_without_migration_side_state(tmp_path: Path) -> None:
    """Repeated bootstrap should not create extra migration bookkeeping state."""

    # Arrange
    db_path = tmp_path / "repeat.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    # Act
    await create_schema(engine)
    await create_schema(engine)
    versions = await list_applied_migrations(engine)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).all()

    # Assert
    table_names = {str(name) for (name,) in rows}
    assert versions == ()
    assert "schema_migration" not in table_names
    await engine.dispose()


async def test_create_schema_materializes_memory_indexes(tmp_path: Path) -> None:
    """Fresh bootstrap should create the memory indexes used by local search and retention."""

    # Arrange
    db_path = tmp_path / "memory-indexes.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    # Act
    await create_schema(engine)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA index_list(memory_item)"))).all()

    # Assert
    index_names = {str(row[1]) for row in rows}
    assert "ix_memory_profile_scope_key" in index_names
    assert "ix_memory_profile_visibility" in index_names
    assert "ix_memory_profile_updated" in index_names
    assert "ix_memory_item_logical_key" in index_names
    await engine.dispose()


async def test_create_schema_materializes_profile_memory_indexes(tmp_path: Path) -> None:
    """Fresh bootstrap should create dedicated profile-memory indexes for pinned core facts."""

    db_path = tmp_path / "profile-memory-indexes.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    await create_schema(engine)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA index_list(profile_memory_item)"))).all()

    index_names = {str(row[1]) for row in rows}
    assert "ix_profile_memory_updated" in index_names
    assert "ix_profile_memory_status_updated" in index_names
    await engine.dispose()


async def test_create_schema_materializes_task_active_owner_unique_index(tmp_path: Path) -> None:
    """Fresh bootstrap should create the unique active-owner Task Flow index."""

    db_path = tmp_path / "task-indexes.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    await create_schema(engine)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA index_list(task)"))).all()

    index_names = {str(row[1]) for row in rows}
    assert "ux_task_active_ai_owner" in index_names
    await engine.dispose()


async def test_create_schema_materializes_run_hot_path_indexes(tmp_path: Path) -> None:
    """Fresh bootstrap should create the run indexes used by progress and cancel lookups."""

    db_path = tmp_path / "run-indexes.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    await create_schema(engine)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA index_list(run)"))).all()

    index_names = {str(row[1]) for row in rows}
    assert "ix_run_profile_session_id" in index_names
    assert "ix_run_profile_session_status_id" in index_names
    await engine.dispose()


async def test_create_schema_backfills_run_hot_path_indexes_for_existing_table(tmp_path: Path) -> None:
    """Repeated bootstrap should backfill run indexes for legacy databases that missed them."""

    db_path = tmp_path / "run-indexes-legacy.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    await create_schema(engine)
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_run_profile_session_id"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_run_profile_session_status_id"))

    await create_schema(engine)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA index_list(run)"))).all()

    index_names = {str(row[1]) for row in rows}
    assert "ix_run_profile_session_id" in index_names
    assert "ix_run_profile_session_status_id" in index_names
    await engine.dispose()


async def test_create_schema_degrades_active_owner_index_when_legacy_duplicates_exist(tmp_path: Path) -> None:
    """Bootstrap should preserve live duplicates but keep the unique guard active for healthy owners."""

    db_path = tmp_path / "task-legacy-duplicates.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    await create_schema(engine)
    factory = create_session_factory(engine)
    async with session_scope(factory) as session:
        repo = ProfileRepository(session)
        await repo.get_or_create_default("default")
        await repo.get_or_create_default("analyst")
    service = TaskFlowService(factory)
    first = await service.create_task(
        profile_id="default",
        title="Legacy active analyst task",
        description="Keep this active after upgrade normalization.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="ai_profile",
        owner_ref="analyst",
    )
    second = await service.create_task(
        profile_id="default",
        title="Legacy duplicate analyst task",
        description="This should be released during schema normalization.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="ai_profile",
        owner_ref="analyst",
    )

    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX ux_task_active_ai_owner"))
        await conn.execute(
            text(
                "UPDATE task "
                "SET status = 'running', claim_token = 'legacy-1', claimed_by = 'worker-1' "
                "WHERE id = :task_id"
            ),
            {"task_id": first.id},
        )
        await conn.execute(
            text(
                "UPDATE task "
                "SET status = 'claimed', claim_token = 'legacy-2', claimed_by = 'worker-2' "
                "WHERE id = :task_id"
            ),
            {"task_id": second.id},
        )

    await create_schema(engine)

    first_after = await service.get_task(profile_id="default", task_id=first.id)
    second_after = await service.get_task(profile_id="default", task_id=second.id)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA index_list(task)"))).all()
        index_sql = (
            await conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'ux_task_active_ai_owner'"
                )
            )
        ).scalar_one()

    index_names = {str(row[1]) for row in rows}
    assert "ux_task_active_ai_owner" in index_names
    index_sql_text = str(index_sql)
    assert "ON task (profile_id, owner_type, owner_ref)" in index_sql_text
    assert "NOT (profile_id = 'default' AND owner_type = 'ai_profile' AND owner_ref = 'analyst')" in index_sql_text
    assert first_after.status == "running"
    assert second_after.status == "claimed"
    assert second_after.last_error_code is None
    await engine.dispose()


async def test_create_schema_materializes_runtime_history_retention_indexes(tmp_path: Path) -> None:
    """Fresh bootstrap should create the indexes used by bounded runtime history cleanup."""

    db_path = tmp_path / "runtime-history-indexes.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    await create_schema(engine)
    async with engine.connect() as conn:
        task_rows = (await conn.execute(text("PRAGMA index_list(task)"))).all()
        task_event_rows = (await conn.execute(text("PRAGMA index_list(task_event)"))).all()
        task_run_rows = (await conn.execute(text("PRAGMA index_list(task_run)"))).all()
        runlog_rows = (await conn.execute(text("PRAGMA index_list(runlog_event)"))).all()

    assert "ix_task_last_run_id" in {str(row[1]) for row in task_rows}
    assert "ix_task_event_created_at" in {str(row[1]) for row in task_event_rows}
    assert "ix_task_run_finished_at" in {str(row[1]) for row in task_run_rows}
    assert "ix_runlog_event_created_at" in {str(row[1]) for row in runlog_rows}
    await engine.dispose()


async def test_prune_runtime_history_removes_only_old_safe_rows(tmp_path: Path) -> None:
    """Bounded runtime cleanup should prune old append-only rows without touching linked task runs."""

    db_path = tmp_path / "runtime-history-prune.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = TaskFlowService(factory)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    old_time = cutoff - timedelta(days=3)
    recent_time = cutoff + timedelta(days=1)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    kept_task = await service.create_task(
        profile_id="default",
        title="Keep linked run",
        description="Retain the run because it still has a surviving task event.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="human",
        owner_ref="cli",
    )
    orphan_task = await service.create_task(
        profile_id="default",
        title="Delete orphaned run",
        description="Allow pruning once the old runtime rows are detached.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="human",
        owner_ref="cli",
    )
    kept_run_id = 0
    orphan_run_id = 0
    async with session_scope(factory) as session:
        await ChatSessionRepository(session).create(session_id="s-1", profile_id="default")
        run = await RunRepository(session).create_run(
            session_id="s-1",
            profile_id="default",
            status="completed",
        )
        await session.flush()
        kept_run = TaskRun(
            task_id=kept_task.id,
            attempt=1,
            owner_type="human",
            owner_ref="cli",
            execution_mode="detached",
            status="completed",
            session_id="taskflow:kept",
            run_id=None,
            worker_id="worker-keep",
            started_at=old_time,
            finished_at=old_time,
            created_at=old_time,
            updated_at=old_time,
        )
        orphan_run = TaskRun(
            task_id=orphan_task.id,
            attempt=1,
            owner_type="human",
            owner_ref="cli",
            execution_mode="detached",
            status="completed",
            session_id="taskflow:orphan",
            run_id=None,
            worker_id="worker-prune",
            started_at=old_time,
            finished_at=old_time,
            created_at=old_time,
            updated_at=old_time,
        )
        session.add_all([kept_run, orphan_run])
        await session.flush()
        kept_run_id = kept_run.id
        orphan_run_id = orphan_run.id
        session.add_all(
            [
                TaskEvent(
                    task_id=kept_task.id,
                    task_run_id=kept_run.id,
                    event_type="comment_added",
                    actor_type="human",
                    actor_ref="cli",
                    message="old event",
                    details_json="{}",
                    created_at=old_time,
                ),
                TaskEvent(
                    task_id=kept_task.id,
                    task_run_id=kept_run.id,
                    event_type="comment_added",
                    actor_type="human",
                    actor_ref="cli",
                    message="recent event",
                    details_json="{}",
                    created_at=recent_time,
                ),
                RunlogEvent(
                    run_id=run.id,
                    session_id="s-1",
                    event_type="llm.call.done",
                    payload_json="{}",
                    created_at=old_time,
                    updated_at=old_time,
                ),
                RunlogEvent(
                    run_id=run.id,
                    session_id="s-1",
                    event_type="llm.call.done",
                    payload_json="{}",
                    created_at=recent_time,
                    updated_at=recent_time,
                ),
            ]
        )
        await session.flush()

    prune_result = await prune_runtime_history(
        engine,
        task_event_before=cutoff,
        task_run_before=cutoff,
        runlog_event_before=cutoff,
        batch_size=10,
    )

    assert prune_result.task_event_count == 1
    assert prune_result.task_run_count == 1
    assert prune_result.runlog_event_count == 1

    async with session_scope(factory) as session:
        remaining_task_events = (await session.execute(text("SELECT COUNT(*) FROM task_event"))).scalar_one()
        remaining_task_runs = (await session.execute(text("SELECT COUNT(*) FROM task_run"))).scalar_one()
        remaining_runlog_events = (await session.execute(text("SELECT COUNT(*) FROM runlog_event"))).scalar_one()
        remaining_task_event_types = (
            await session.execute(text("SELECT event_type FROM task_event ORDER BY id ASC"))
        ).scalars().all()
        kept_run_exists = await session.get(TaskRun, kept_run_id)
        orphan_run_exists = await session.get(TaskRun, orphan_run_id)

    assert int(remaining_task_events) == 3
    assert int(remaining_task_runs) == 1
    assert int(remaining_runlog_events) == 1
    assert list(remaining_task_event_types).count("comment_added") == 1
    assert list(remaining_task_event_types).count("created") == 2
    assert kept_run_exists is not None
    assert orphan_run_exists is None
    await engine.dispose()


async def test_prune_runtime_history_keeps_old_task_run_with_newer_task_event_reference(
    tmp_path: Path,
) -> None:
    """Cleanup should preserve old task runs that still have newer task_event FK references."""

    db_path = tmp_path / "runtime-history-keep-referenced-run.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = TaskFlowService(factory)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    old_time = cutoff - timedelta(days=3)
    recent_time = cutoff + timedelta(days=1)

    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    task = await service.create_task(
        profile_id="default",
        title="Keep run referenced by newer task event",
        description="Preserve old task run rows while newer task events still reference them.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="human",
        owner_ref="cli",
    )

    referenced_run_id = 0
    async with session_scope(factory) as session:
        referenced_run = TaskRun(
            task_id=task.id,
            attempt=1,
            owner_type="human",
            owner_ref="cli",
            execution_mode="detached",
            status="completed",
            session_id="taskflow:referenced",
            run_id=None,
            worker_id="worker-referenced",
            started_at=old_time,
            finished_at=old_time,
            created_at=old_time,
            updated_at=old_time,
        )
        session.add(referenced_run)
        await session.flush()
        referenced_run_id = int(referenced_run.id)
        session.add(
            TaskEvent(
                task_id=task.id,
                task_run_id=referenced_run.id,
                event_type="comment_added",
                actor_type="human",
                actor_ref="cli",
                message="newer event keeps task_run referenced",
                details_json="{}",
                created_at=recent_time,
            )
        )
        await session.flush()

    prune_result = await prune_runtime_history(
        engine,
        task_event_before=cutoff,
        task_run_before=cutoff,
        runlog_event_before=cutoff,
        batch_size=10,
    )

    assert prune_result.task_event_count == 0
    assert prune_result.task_run_count == 0
    assert prune_result.runlog_event_count == 0

    async with session_scope(factory) as session:
        referenced_run_exists = await session.get(TaskRun, referenced_run_id)

    assert referenced_run_exists is not None
    await engine.dispose()


async def test_prune_runtime_history_rejects_non_positive_batch_size(tmp_path: Path) -> None:
    """Bounded cleanup should fail fast when batch_size is not positive."""

    db_path = tmp_path / "runtime-history-invalid-batch-size.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)
    try:
        await create_schema(engine)
        cutoff = datetime.now(timezone.utc)

        for invalid_batch_size in (0, -1):
            try:
                await prune_runtime_history(
                    engine,
                    task_event_before=cutoff,
                    task_run_before=cutoff,
                    runlog_event_before=cutoff,
                    batch_size=invalid_batch_size,
                )
            except ValueError as exc:
                assert str(exc) == "batch_size must be >= 1"
            else:
                raise AssertionError(
                    f"Expected ValueError for batch_size={invalid_batch_size}"
                )
    finally:
        await engine.dispose()


async def test_prune_runtime_history_keeps_task_last_run_reference(tmp_path: Path) -> None:
    """Bounded cleanup must not delete a finished orphan run still referenced as the task's last run."""

    db_path = tmp_path / "runtime-history-keep-last-run.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)
    await create_schema(engine)
    factory = create_session_factory(engine)
    service = TaskFlowService(factory)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    old_time = cutoff - timedelta(days=3)
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")

    task = await service.create_task(
        profile_id="default",
        title="Keep last run reference",
        description="Do not prune the last_run_id reference automatically.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="human",
        owner_ref="cli",
    )
    protected_run_id = 0
    async with session_scope(factory) as session:
        await ProfileRepository(session).get_or_create_default("default")
        protected_run = TaskRun(
            task_id=task.id,
            attempt=1,
            owner_type="human",
            owner_ref="cli",
            execution_mode="detached",
            status="completed",
            session_id="taskflow:protected",
            run_id=None,
            worker_id="worker-protected",
            started_at=old_time,
            finished_at=old_time,
            created_at=old_time,
            updated_at=old_time,
        )
        session.add(protected_run)
        await session.flush()
        protected_run_id = protected_run.id
        await session.execute(
            text("UPDATE task SET last_run_id = :run_id WHERE id = :task_id"),
            {"run_id": protected_run.id, "task_id": task.id},
        )

    prune_result = await prune_runtime_history(
        engine,
        task_event_before=None,
        task_run_before=cutoff,
        runlog_event_before=None,
        batch_size=10,
    )

    assert prune_result.task_run_count == 0
    async with session_scope(factory) as session:
        protected_run_exists = await session.get(TaskRun, protected_run_id)
        task_last_run_id = (
            await session.execute(text("SELECT last_run_id FROM task WHERE id = :task_id"), {"task_id": task.id})
        ).scalar_one()

    assert protected_run_exists is not None
    assert int(task_last_run_id) == protected_run_id
    await engine.dispose()


async def test_sqlite_connect_degrades_gracefully_when_wal_pragma_fails(tmp_path: Path) -> None:
    """Engine connect should keep working when WAL activation is unsupported or read-only."""

    db_path = tmp_path / "wal-fallback.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    wal_attempts = {"count": 0}
    original_connect = sqlite3.connect

    class _CursorWrapper:
        def __init__(self, inner: sqlite3.Cursor) -> None:
            self._inner = inner

        def execute(self, sql: str, parameters: object = ()) -> object:
            if sql == "PRAGMA journal_mode=WAL":
                wal_attempts["count"] += 1
                raise sqlite3.OperationalError("attempt to write a readonly database")
            return self._inner.execute(sql, parameters)

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    class _ConnectionWrapper:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def cursor(self, *args: object, **kwargs: object) -> _CursorWrapper:
            return _CursorWrapper(self._inner.cursor(*args, **kwargs))

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

    def _wrapped_connect(*args: object, **kwargs: object) -> _ConnectionWrapper:
        return _ConnectionWrapper(original_connect(*args, **kwargs))

    sqlite3.connect = _wrapped_connect
    try:
        await create_schema(engine)
        reachable = await ping(engine)
        async with engine.connect() as conn:
            foreign_keys_enabled = (await conn.execute(text("PRAGMA foreign_keys"))).scalar_one()
            busy_timeout_ms = (await conn.execute(text("PRAGMA busy_timeout"))).scalar_one()
            journal_mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar_one()
        assert reachable is True
        assert wal_attempts["count"] >= 1
        assert int(foreign_keys_enabled) == 1
        assert int(busy_timeout_ms) == 5000
        assert str(journal_mode).lower() != ""
    finally:
        sqlite3.connect = original_connect
        await engine.dispose()
async def test_create_schema_backfills_task_description_from_legacy_prompt_column(
    tmp_path: Path,
) -> None:
    """Legacy task tables should gain the description column and preserve prompt text."""

    db_path = tmp_path / "legacy_task_description.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE profile (
                    id VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    is_default BOOLEAN NOT NULL DEFAULT 0,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE task (
                    id VARCHAR(64) PRIMARY KEY,
                    profile_id VARCHAR(64) NOT NULL,
                    flow_id VARCHAR(64),
                    title VARCHAR(255) NOT NULL,
                    prompt TEXT NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'todo',
                    priority INTEGER NOT NULL DEFAULT 50,
                    due_at DATETIME,
                    ready_at DATETIME,
                    owner_type VARCHAR(32) NOT NULL,
                    owner_ref VARCHAR(255) NOT NULL,
                    reviewer_type VARCHAR(32),
                    reviewer_ref VARCHAR(255),
                    source_type VARCHAR(64) NOT NULL DEFAULT 'manual',
                    source_ref VARCHAR(255),
                    created_by_type VARCHAR(32) NOT NULL,
                    created_by_ref VARCHAR(255) NOT NULL,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    requires_review BOOLEAN NOT NULL DEFAULT 0,
                    blocked_reason_code VARCHAR(64),
                    blocked_reason_text TEXT,
                    claim_token VARCHAR(64),
                    claimed_by VARCHAR(128),
                    lease_until DATETIME,
                    current_attempt INTEGER NOT NULL DEFAULT 0,
                    last_session_id VARCHAR(128),
                    last_run_id INTEGER,
                    last_error_code VARCHAR(64),
                    last_error_text TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(profile_id) REFERENCES profile(id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO profile (id, name, is_default, status, settings_json)
                VALUES ('default', 'Default', 1, 'active', '{}')
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO task (
                    id, profile_id, title, prompt, status, owner_type, owner_ref,
                    created_by_type, created_by_ref
                )
                VALUES (
                    'task_legacy', 'default', 'Legacy task', 'Legacy prompt body',
                    'todo', 'human', 'cli_user:alice', 'human', 'cli'
                )
                """
            )
        )

    await create_schema(engine)

    async with engine.connect() as conn:
        columns = {
            str(row[1])
            for row in (await conn.execute(text("PRAGMA table_info('task')"))).fetchall()
        }
        description = (
            await conn.execute(text("SELECT description FROM task WHERE id = 'task_legacy'"))
        ).scalar_one()

    assert "description" in columns
    assert description == "Legacy prompt body"
    await engine.dispose()


async def test_create_schema_allows_new_task_inserts_after_legacy_prompt_upgrade(
    tmp_path: Path,
) -> None:
    """Legacy prompt-only task tables should accept new description-based inserts after upgrade."""

    db_path = tmp_path / "legacy_task_prompt_not_null.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE profile (
                    id VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    is_default BOOLEAN NOT NULL DEFAULT 0,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE task (
                    id VARCHAR(64) PRIMARY KEY,
                    profile_id VARCHAR(64) NOT NULL,
                    flow_id VARCHAR(64),
                    title VARCHAR(255) NOT NULL,
                    prompt TEXT NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'todo',
                    priority INTEGER NOT NULL DEFAULT 50,
                    due_at DATETIME,
                    ready_at DATETIME,
                    owner_type VARCHAR(32) NOT NULL,
                    owner_ref VARCHAR(255) NOT NULL,
                    reviewer_type VARCHAR(32),
                    reviewer_ref VARCHAR(255),
                    source_type VARCHAR(64) NOT NULL DEFAULT 'manual',
                    source_ref VARCHAR(255),
                    created_by_type VARCHAR(32) NOT NULL,
                    created_by_ref VARCHAR(255) NOT NULL,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    requires_review BOOLEAN NOT NULL DEFAULT 0,
                    blocked_reason_code VARCHAR(64),
                    blocked_reason_text TEXT,
                    claim_token VARCHAR(64),
                    claimed_by VARCHAR(128),
                    lease_until DATETIME,
                    current_attempt INTEGER NOT NULL DEFAULT 0,
                    last_session_id VARCHAR(128),
                    last_run_id INTEGER,
                    last_error_code VARCHAR(64),
                    last_error_text TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(profile_id) REFERENCES profile(id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO profile (id, name, is_default, status, settings_json)
                VALUES ('default', 'Default', 1, 'active', '{}')
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO task (
                    id, profile_id, title, prompt, status, owner_type, owner_ref,
                    created_by_type, created_by_ref
                )
                VALUES (
                    'task_legacy', 'default', 'Legacy task', 'Legacy prompt body',
                    'todo', 'human', 'cli_user:alice', 'human', 'cli'
                )
                """
            )
        )

    await create_schema(engine)

    service = TaskFlowService(create_session_factory(engine))
    created = await service.create_task(
        profile_id="default",
        title="New description task",
        description="This insert should succeed after the upgrade.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="ai_profile",
        owner_ref="default",
    )

    assert created.description == "This insert should succeed after the upgrade."
    async with engine.connect() as conn:
        columns = {
            str(row[1])
            for row in (await conn.execute(text("PRAGMA table_info('task')"))).fetchall()
        }
    assert "prompt" not in columns
    await engine.dispose()


async def test_task_flow_create_task_reports_legacy_task_schema_mismatch(tmp_path: Path) -> None:
    """Legacy task tables should raise one structured compatibility error instead of raw DB failure."""

    db_path = tmp_path / "legacy_task_schema_mismatch.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE profile (
                    id VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    is_default BOOLEAN NOT NULL DEFAULT 0,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE task (
                    id VARCHAR(64) PRIMARY KEY,
                    profile_id VARCHAR(64) NOT NULL,
                    flow_id VARCHAR(64),
                    title VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'todo',
                    priority INTEGER NOT NULL DEFAULT 50,
                    due_at DATETIME,
                    ready_at DATETIME,
                    owner_type VARCHAR(32) NOT NULL,
                    owner_ref VARCHAR(255) NOT NULL,
                    reviewer_type VARCHAR(32),
                    reviewer_ref VARCHAR(255),
                    source_type VARCHAR(64) NOT NULL DEFAULT 'manual',
                    source_ref VARCHAR(255),
                    created_by_type VARCHAR(32) NOT NULL,
                    created_by_ref VARCHAR(255) NOT NULL,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    requires_review BOOLEAN NOT NULL DEFAULT 0,
                    blocked_reason_code VARCHAR(64),
                    blocked_reason_text TEXT,
                    claim_token VARCHAR(64),
                    claimed_by VARCHAR(128),
                    lease_until DATETIME,
                    current_attempt INTEGER NOT NULL DEFAULT 0,
                    last_session_id VARCHAR(128),
                    last_run_id INTEGER,
                    last_error_code VARCHAR(64),
                    last_error_text TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(profile_id) REFERENCES profile(id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE task_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id VARCHAR(64) NOT NULL,
                    task_run_id INTEGER,
                    event_type VARCHAR(64) NOT NULL,
                    actor_type VARCHAR(32),
                    actor_ref VARCHAR(255),
                    message TEXT,
                    from_status VARCHAR(32),
                    to_status VARCHAR(32),
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO profile (id, name, is_default, status, settings_json)
                VALUES ('default', 'Default', 1, 'active', '{}')
                """
            )
        )

    service = TaskFlowService(create_session_factory(engine))

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.create_task(
            profile_id="default",
            title="Legacy task schema mismatch",
            description="This should surface a compatibility error.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
            session_id="session-1",
            session_profile_id="default",
        )

    assert exc_info.value.error_code == "task_flow_schema_incompatible"
    assert "AFKBOT >= 1.4.2" in exc_info.value.reason
    await engine.dispose()


async def test_task_flow_create_task_reports_legacy_task_event_schema_mismatch(tmp_path: Path) -> None:
    """Legacy task_event tables should raise one structured compatibility error instead of raw DB failure."""

    db_path = tmp_path / "legacy_task_event_schema_mismatch.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE profile (
                    id VARCHAR(64) PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    is_default BOOLEAN NOT NULL DEFAULT 0,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE task (
                    id VARCHAR(64) PRIMARY KEY,
                    profile_id VARCHAR(64) NOT NULL,
                    flow_id VARCHAR(64),
                    title VARCHAR(255) NOT NULL,
                    description TEXT NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'todo',
                    priority INTEGER NOT NULL DEFAULT 50,
                    due_at DATETIME,
                    ready_at DATETIME,
                    owner_type VARCHAR(32) NOT NULL,
                    owner_ref VARCHAR(255) NOT NULL,
                    reviewer_type VARCHAR(32),
                    reviewer_ref VARCHAR(255),
                    source_type VARCHAR(64) NOT NULL DEFAULT 'manual',
                    source_ref VARCHAR(255),
                    created_by_type VARCHAR(32) NOT NULL,
                    created_by_ref VARCHAR(255) NOT NULL,
                    labels_json TEXT NOT NULL DEFAULT '[]',
                    requires_review BOOLEAN NOT NULL DEFAULT 0,
                    blocked_reason_code VARCHAR(64),
                    blocked_reason_text TEXT,
                    claim_token VARCHAR(64),
                    claimed_by VARCHAR(128),
                    lease_until DATETIME,
                    current_attempt INTEGER NOT NULL DEFAULT 0,
                    last_session_id VARCHAR(128),
                    last_session_profile_id VARCHAR(120),
                    last_run_id INTEGER,
                    last_error_code VARCHAR(64),
                    last_error_text TEXT,
                    started_at DATETIME,
                    finished_at DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(profile_id) REFERENCES profile(id)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE task_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id VARCHAR(64) NOT NULL,
                    task_run_id INTEGER,
                    event_type VARCHAR(64) NOT NULL,
                    actor_type VARCHAR(32),
                    actor_ref VARCHAR(255),
                    message TEXT,
                    from_status VARCHAR(32),
                    to_status VARCHAR(32),
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO profile (id, name, is_default, status, settings_json)
                VALUES ('default', 'Default', 1, 'active', '{}')
                """
            )
        )

    service = TaskFlowService(create_session_factory(engine))

    with pytest.raises(TaskFlowServiceError) as exc_info:
        await service.create_task(
            profile_id="default",
            title="Legacy task_event schema mismatch",
            description="This should surface a compatibility error.",
            created_by_type="human",
            created_by_ref="cli",
            owner_type="ai_profile",
            owner_ref="default",
        )

    assert exc_info.value.error_code == "task_flow_schema_incompatible"
    assert "afk upgrade apply" in exc_info.value.reason
    await engine.dispose()
