"""Contract tests for the clean SQLite bootstrap baseline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import warnings

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects import postgresql, sqlite

import afkbot.db.engine as db_engine_module
from afkbot.db.bootstrap import create_schema, list_applied_migrations, ping
from afkbot.db.bootstrap_runtime import (
    ManagedRuntimeSchemaError,
    _requires_managed_runtime_schema_validation,
    ensure_task_runtime_schema,
    prune_runtime_history,
)
from afkbot.db.engine import create_engine
from afkbot.db.postgres_contract import (
    PostgresBootstrapContractError,
    build_database_per_bot_contract,
    render_database_per_bot_bootstrap_plan,
)
from afkbot.db.runtime import DatabaseRuntime, resolve_session_resources
from afkbot.services.managed_database_guard import (
    ManagedDatabaseGuardError,
    validate_managed_database_runtime,
)
from afkbot.db.session import (
    SerializedSQLiteSession,
    create_session_factory,
    session_scope,
    sqlite_write_gate_for_engine,
)
from afkbot.db.upsert import upsert_insert_for_dialect
from afkbot.models.chat_session_compaction import ChatSessionCompaction
from afkbot.models.knowledge_artifact import KnowledgeArtifact
from afkbot.models.runlog_event import RunlogEvent
from afkbot.models.task import Task
from afkbot.models.task_event import TaskEvent
from afkbot.models.task_run import TaskRun
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.repositories.run_repo import RunRepository
from afkbot.repositories.task_flow_repo import _apply_task_claim_locking_for_dialect
from afkbot.settings import Settings
from afkbot.services.task_flow import TaskFlowServiceError
from afkbot.services.task_flow.service import TaskFlowService


def test_postgres_database_per_bot_contract_renders_safe_role_plan() -> None:
    """Database-per-bot bootstrap contract should keep admin SQL outside runtime settings."""

    contract = build_database_per_bot_contract(bot_id="Bot-42")
    plan = render_database_per_bot_bootstrap_plan(contract)

    assert contract.database_name == "afkbot_bot_42"
    assert contract.migrator_role_name == "afkbot_bot_42_migrator"
    assert contract.runtime_role_name == "afkbot_bot_42_runtime"
    assert "PASSWORD :migrator_role_password" in plan.cluster_statements[0]
    assert "PASSWORD :runtime_role_password" in plan.cluster_statements[1]
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in plan.cluster_statements[1]
    assert (
        'CREATE DATABASE "afkbot_bot_42" OWNER "afkbot_bot_42_migrator"'
        in plan.cluster_statements[2]
    )
    assert (
        'GRANT CONNECT ON DATABASE "afkbot_bot_42" TO "afkbot_bot_42_runtime"'
        in plan.cluster_statements[3]
    )
    assert plan.migration_statements[0] == "SELECT pg_advisory_lock(hashtext(current_database()))"
    assert "CREATE TABLE IF NOT EXISTS afkbot_schema_migration" in plan.migration_statements[1]
    assert "version INTEGER PRIMARY KEY" in plan.migration_statements[1]
    assert plan.database_statements[0] == "REVOKE ALL ON SCHEMA public FROM PUBLIC"
    assert 'GRANT USAGE ON SCHEMA "public" TO "afkbot_bot_42_runtime"' in plan.database_statements
    assert "GRANT USAGE, CREATE" not in "\n".join(plan.database_statements)


def test_postgres_database_per_bot_contract_rejects_unsafe_identifiers() -> None:
    """Identifier validation should reject SQL injection-shaped names."""

    with pytest.raises(PostgresBootstrapContractError) as exc:
        build_database_per_bot_contract(
            bot_id="bot-1",
            database_name='afkbot";DROP DATABASE postgres;--',
        )

    assert exc.value.error_code == "postgres_identifier_invalid"


def test_managed_database_settings_require_workspace_sqlite(tmp_path: Path) -> None:
    """Managed mode keeps bot state in workspace-local SQLite."""

    sqlite_settings = Settings(
        root_dir=tmp_path,
        deployment_mode="managed",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'managed.db'}",
    )
    validate_managed_database_runtime(sqlite_settings)

    postgres_settings = Settings(
        root_dir=tmp_path,
        deployment_mode="managed",
        db_url="postgresql+asyncpg://bot_role:secret@db.example.com/afkbot_bot_1",
    )
    with pytest.raises(ManagedDatabaseGuardError) as postgres_exc:
        validate_managed_database_runtime(postgres_settings)
    assert postgres_exc.value.error_code == "managed_database_sqlite_required"

    outside_settings = Settings(
        root_dir=tmp_path / "workspace",
        deployment_mode="managed",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'outside.db'}",
    )
    with pytest.raises(ManagedDatabaseGuardError) as outside_exc:
        validate_managed_database_runtime(outside_settings)
    assert outside_exc.value.error_code == "managed_database_outside_workspace"

    memory_settings = Settings(
        root_dir=tmp_path,
        deployment_mode="managed",
        db_url="sqlite+aiosqlite:///:memory:",
    )
    with pytest.raises(ManagedDatabaseGuardError) as memory_exc:
        validate_managed_database_runtime(memory_settings)
    assert memory_exc.value.error_code == "managed_database_sqlite_file_required"


def test_managed_runtime_schema_creation_uses_sqlite(tmp_path: Path) -> None:
    """Managed runtimes create/upgrade local SQLite schema in their workspace."""

    settings = Settings(
        root_dir=tmp_path,
        deployment_mode="managed",
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'managed.db'}",
    )

    assert (
        _requires_managed_runtime_schema_validation(settings=settings, dialect_name="sqlite")
        is False
    )
    with pytest.raises(ManagedRuntimeSchemaError) as exc:
        _requires_managed_runtime_schema_validation(settings=settings, dialect_name="postgresql")
    assert exc.value.error_code == "managed_database_sqlite_required"
    assert (
        _requires_managed_runtime_schema_validation(
            settings=Settings(root_dir=tmp_path),
            dialect_name="sqlite",
        )
        is False
    )


def test_dialect_aware_upsert_insert_compiles_for_sqlite_and_postgres() -> None:
    """Hot-path upserts should not be tied to the SQLite insert implementation."""

    payload = {
        "profile_id": "default",
        "session_id": "session-1",
        "summary_text": "summary",
        "compacted_until_turn_id": 1,
        "source_turn_count": 3,
        "strategy": "rolling",
    }
    sqlite_statement = upsert_insert_for_dialect(
        dialect_name="sqlite",
        model=ChatSessionCompaction,
    ).values(**payload)
    sqlite_statement = sqlite_statement.on_conflict_do_update(
        index_elements=[
            ChatSessionCompaction.session_id,
            ChatSessionCompaction.profile_id,
        ],
        set_={"summary_text": payload["summary_text"]},
    )
    postgres_statement = upsert_insert_for_dialect(
        dialect_name="postgresql",
        model=ChatSessionCompaction,
    ).values(**payload)
    postgres_statement = postgres_statement.on_conflict_do_update(
        index_elements=[
            ChatSessionCompaction.session_id,
            ChatSessionCompaction.profile_id,
        ],
        set_={"summary_text": payload["summary_text"]},
    )

    assert "ON CONFLICT" in str(sqlite_statement.compile(dialect=sqlite.dialect()))
    assert "ON CONFLICT" in str(postgres_statement.compile(dialect=postgresql.dialect()))


def test_knowledge_artifact_upsert_conflict_targets_compile_for_sqlite_and_postgres() -> None:
    """Knowledge artifact upserts should keep both supported dialect paths valid."""

    payload = {
        "profile_id": "default",
        "scope_type": "task",
        "scope_id": "task-1",
        "artifact_kind": "task_crystal",
        "title": "Task outcome",
        "summary": "Summary",
        "source_refs_json": "[]",
        "tags_json": "[]",
        "confidence": 0.75,
        "confirmed": True,
        "source_fingerprint": "a" * 64,
        "dedupe_key": "task_crystal:task-1:manual:completed",
        "status": "active",
    }
    sqlite_statement = upsert_insert_for_dialect(
        dialect_name="sqlite",
        model=KnowledgeArtifact,
    ).values(**payload)
    sqlite_statement = sqlite_statement.on_conflict_do_update(
        index_elements=["profile_id", "dedupe_key"],
        set_={"summary": payload["summary"]},
    )
    postgres_statement = upsert_insert_for_dialect(
        dialect_name="postgresql",
        model=KnowledgeArtifact,
    ).values(**payload)
    postgres_statement = postgres_statement.on_conflict_do_update(
        constraint="uq_knowledge_artifact_profile_dedupe",
        set_={"summary": payload["summary"]},
    )

    assert "ON CONFLICT" in str(sqlite_statement.compile(dialect=sqlite.dialect()))
    assert "ON CONSTRAINT uq_knowledge_artifact_profile_dedupe" in str(
        postgres_statement.compile(dialect=postgresql.dialect())
    )


def test_task_claim_uses_skip_locked_only_for_postgres() -> None:
    """Cloud Task Flow workers should avoid waiting on the same candidate row."""

    base_statement = select(Task.id).limit(1)

    postgres_statement = _apply_task_claim_locking_for_dialect(
        base_statement,
        dialect_name="postgresql",
    )
    sqlite_statement = _apply_task_claim_locking_for_dialect(
        base_statement,
        dialect_name="sqlite",
    )

    postgres_sql = str(postgres_statement.compile(dialect=postgresql.dialect()))
    sqlite_sql = str(sqlite_statement.compile(dialect=sqlite.dialect()))
    assert "FOR UPDATE" in postgres_sql
    assert "SKIP LOCKED" in postgres_sql
    assert "FOR UPDATE" not in sqlite_sql


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


async def test_create_engine_supports_in_memory_sqlite(tmp_path: Path) -> None:
    """Pool sizing options should not break SQLite StaticPool URLs."""

    settings = Settings(db_url="sqlite+aiosqlite:///:memory:", root_dir=tmp_path)
    engine = create_engine(settings)

    try:
        await create_schema(engine)
        assert await ping(engine) is True
    finally:
        await engine.dispose()


async def test_sqlite_writes_are_serialized_across_factories(tmp_path: Path) -> None:
    """SQLite writes for one database file should not execute concurrently."""

    db_path = tmp_path / "serialized-scopes.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    first_engine = create_engine(settings)
    second_engine = create_engine(settings)
    second_factory = create_session_factory(second_engine)
    lock_path = db_path.with_suffix(f"{db_path.suffix}.afkbot.lock")
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    entered: list[str] = []

    async def _hold_first_write_gate() -> None:
        async with sqlite_write_gate_for_engine(first_engine):
            entered.append("first")
            first_entered.set()
            await release_first.wait()

    async def _run_second_write() -> None:
        await first_entered.wait()
        async with session_scope(second_factory) as session:
            await session.execute(text("CREATE TABLE IF NOT EXISTS gate_probe (id INTEGER)"))
            entered.append("second")

    first_task = asyncio.create_task(_hold_first_write_gate())
    second_task = asyncio.create_task(_run_second_write())
    try:
        await asyncio.wait_for(first_entered.wait(), timeout=1.0)
        assert lock_path.exists()
        await asyncio.sleep(0.05)
        assert entered == ["first"]

        release_first.set()
        await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=1.0)
        assert entered == ["first", "second"]
    finally:
        release_first.set()
        for task in (first_task, second_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(first_task, second_task, return_exceptions=True)
        await first_engine.dispose()
        await second_engine.dispose()


async def test_sqlite_write_gate_is_reentrant_only_within_current_task(tmp_path: Path) -> None:
    """Child asyncio tasks must not inherit a parent's SQLite write lease."""

    db_path = tmp_path / "task-local-gate.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    child_started = asyncio.Event()
    child_done = asyncio.Event()
    child_task: asyncio.Task[None] | None = None

    async def _child_write() -> None:
        child_started.set()
        async with session_scope(session_factory) as session:
            await session.execute(text("CREATE TABLE IF NOT EXISTS task_gate_probe (id INTEGER)"))
            await session.execute(text("INSERT INTO task_gate_probe (id) VALUES (1)"))
        child_done.set()

    try:
        async with sqlite_write_gate_for_engine(engine):
            async with sqlite_write_gate_for_engine(engine):
                pass

            child_task = asyncio.create_task(_child_write())
            await asyncio.wait_for(child_started.wait(), timeout=1.0)
            await asyncio.sleep(0.05)
            assert not child_done.is_set()

        await asyncio.wait_for(child_done.wait(), timeout=1.0)
        await child_task
    finally:
        if child_task is not None and not child_task.done():
            child_task.cancel()
            await asyncio.gather(child_task, return_exceptions=True)
        await engine.dispose()


async def test_create_session_factory_uses_serialized_sqlite_session(tmp_path: Path) -> None:
    """All factory-created SQLite sessions should carry the write gate."""

    db_path = tmp_path / "serialized-session-class.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            assert isinstance(session, SerializedSQLiteSession)
    finally:
        await engine.dispose()


async def test_sqlite_wal_activation_uses_shared_file_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection-time WAL writes should share the same SQLite file lock."""

    db_path = tmp_path / "wal-lock.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    lock_path = db_path.with_suffix(f"{db_path.suffix}.afkbot.lock")
    events: list[tuple[str, Path | None]] = []

    @contextmanager
    def _record_file_lock(path: Path | None, *, blocking: bool = True):
        events.append((f"lock:{blocking}", path))
        try:
            yield True
        finally:
            events.append((f"unlock:{blocking}", path))

    def _record_wal_attempt(_cursor: object) -> None:
        events.append(("wal", None))
        return None

    monkeypatch.setattr(db_engine_module, "sqlite_file_lock_sync", _record_file_lock)
    monkeypatch.setattr(db_engine_module, "_try_enable_sqlite_wal", _record_wal_attempt)
    engine = create_engine(settings)

    try:
        assert await ping(engine) is True
        assert events == [
            ("lock:False", lock_path),
            ("wal", None),
            ("unlock:False", lock_path),
        ]
    finally:
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
        assert not [
            warning for warning in caught if issubclass(warning.category, DeprecationWarning)
        ]
    finally:
        await engine.dispose()


def test_create_engine_configures_postgres_pool_and_timeouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Managed Postgres connections should have bounded pool and server-side timeouts."""

    captured: dict[str, object] = {}

    class _DummySyncEngine:
        pass

    class _DummyEngine:
        sync_engine = _DummySyncEngine()

    def _fake_create_async_engine(db_url: str, **kwargs: object) -> _DummyEngine:
        captured["db_url"] = db_url
        captured.update(kwargs)
        return _DummyEngine()

    monkeypatch.setattr(db_engine_module, "create_async_engine", _fake_create_async_engine)

    settings = Settings(
        root_dir=tmp_path,
        db_url="postgresql+asyncpg://bot_role:secret@db.example.com/afkbot_bot_1",
        db_pool_size=8,
        db_max_overflow=2,
        db_pool_timeout_sec=7,
        db_statement_timeout_ms=12_000,
        db_idle_in_transaction_timeout_ms=9_000,
        db_application_name="afkbot-test",
    )

    db_engine_module.create_engine(settings)

    connect_args = captured["connect_args"]
    assert captured["pool_pre_ping"] is True
    assert captured["pool_size"] == 8
    assert captured["max_overflow"] == 2
    assert captured["pool_timeout"] == 7
    assert isinstance(connect_args, dict)
    server_settings = connect_args["server_settings"]
    assert isinstance(server_settings, dict)
    assert server_settings["application_name"] == "afkbot-test"
    assert server_settings["statement_timeout"] == "12000"
    assert server_settings["idle_in_transaction_session_timeout"] == "9000"


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


async def test_create_schema_adds_session_store_tables_to_existing_runtime_db(
    tmp_path: Path,
) -> None:
    """Old runtime DBs should gain JSONL outbox tables without losing existing rows."""

    db_path = tmp_path / "legacy-runtime.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    try:
        await create_schema(engine)
        async with session_scope(create_session_factory(engine)) as session:
            await ProfileRepository(session).get_or_create_default("default")
            await ChatSessionRepository(session).create(
                session_id="s-existing",
                profile_id="default",
            )
            run = await RunRepository(session).create_run(
                session_id="s-existing",
                profile_id="default",
            )

        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE session_store_outbox"))
            await conn.execute(text("DROP TABLE session_store_sequence"))

        await create_schema(engine)

        async with engine.connect() as conn:
            table_rows = (
                await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            ).all()
            run_count = (
                await conn.execute(text("SELECT COUNT(*) FROM run WHERE id = :id"), {"id": run.id})
            ).scalar_one()

        table_names = {str(name) for (name,) in table_rows}
        assert {"session_store_outbox", "session_store_sequence"} <= table_names
        assert int(run_count) == 1
    finally:
        await engine.dispose()


async def test_database_runtime_adds_session_store_tables_to_existing_runtime_db(
    tmp_path: Path,
) -> None:
    """Shared runtime schema initialization should also upgrade old DB files."""

    db_path = tmp_path / "legacy-runtime-shared.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    setup_engine = create_engine(settings)
    try:
        await create_schema(setup_engine)
        async with setup_engine.begin() as conn:
            await conn.execute(text("DROP TABLE session_store_outbox"))
            await conn.execute(text("DROP TABLE session_store_sequence"))
    finally:
        await setup_engine.dispose()

    runtime = DatabaseRuntime.create(settings)
    try:
        await runtime.ensure_schema()
        resources = await resolve_session_resources(
            shared_session_factory=runtime.session_factory,
            settings=settings,
        )
        async with session_scope(resources.session_factory) as session:
            await ProfileRepository(session).get_or_create_default("default")
        async with runtime.engine.connect() as conn:
            table_rows = (
                await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            ).all()
        table_names = {str(name) for (name,) in table_rows}
        assert {"session_store_outbox", "session_store_sequence"} <= table_names
    finally:
        await runtime.dispose()


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


async def test_create_schema_materializes_knowledge_artifact_indexes(tmp_path: Path) -> None:
    """Fresh bootstrap should create indexes for dark-launch Task Flow knowledge artifacts."""

    db_path = tmp_path / "knowledge-artifact-indexes.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    await create_schema(engine)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA index_list(knowledge_artifact)"))).all()

    index_names = {str(row[1]) for row in rows}
    assert any(bool(row[2]) for row in rows)
    assert "ix_knowledge_artifact_profile_task_active" in index_names
    assert "ix_knowledge_artifact_scope" in index_names
    await engine.dispose()


async def test_create_schema_materializes_task_active_owner_unique_index(tmp_path: Path) -> None:
    """Fresh bootstrap should create the unique active-owner Task Flow index."""

    db_path = tmp_path / "task-indexes.db"
    settings = Settings(db_url=f"sqlite+aiosqlite:///{db_path}", root_dir=tmp_path)
    engine = create_engine(settings)

    await create_schema(engine)
    async with engine.connect() as conn:
        rows = (await conn.execute(text("PRAGMA index_list(task)"))).all()
        column_rows = (await conn.execute(text("PRAGMA table_info(task)"))).all()

    index_names = {str(row[1]) for row in rows}
    column_names = {str(row[1]) for row in column_rows}
    assert "ux_task_active_ai_owner" in index_names
    assert "source_transport" in column_names
    assert "source_channel_profile" in column_names
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


async def test_create_schema_backfills_run_hot_path_indexes_for_existing_table(
    tmp_path: Path,
) -> None:
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


async def test_create_schema_degrades_active_owner_index_when_legacy_duplicates_exist(
    tmp_path: Path,
) -> None:
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
    assert (
        "NOT (profile_id = 'default' AND owner_type = 'ai_profile' AND owner_ref = 'analyst')"
        in index_sql_text
    )
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
        remaining_task_events = (
            await session.execute(text("SELECT COUNT(*) FROM task_event"))
        ).scalar_one()
        remaining_task_runs = (
            await session.execute(text("SELECT COUNT(*) FROM task_run"))
        ).scalar_one()
        remaining_runlog_events = (
            await session.execute(text("SELECT COUNT(*) FROM runlog_event"))
        ).scalar_one()
        remaining_task_event_types = (
            (await session.execute(text("SELECT event_type FROM task_event ORDER BY id ASC")))
            .scalars()
            .all()
        )
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
                raise AssertionError(f"Expected ValueError for batch_size={invalid_batch_size}")
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
            await session.execute(
                text("SELECT last_run_id FROM task WHERE id = :task_id"), {"task_id": task.id}
            )
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


async def test_create_schema_preserves_existing_description_over_legacy_prompt(
    tmp_path: Path,
) -> None:
    """When both columns exist, migration should keep filled description and fallback only blanks."""

    db_path = tmp_path / "legacy_task_description_precedence.db"
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
                    settings_json TEXT NOT NULL DEFAULT '{}'
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
                    title VARCHAR(255) NOT NULL,
                    prompt TEXT NOT NULL,
                    description TEXT NOT NULL
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
                INSERT INTO task (id, profile_id, title, prompt, description)
                VALUES
                    ('task_keep_description', 'default', 'Keep description', 'Legacy prompt', 'Canonical description'),
                    ('task_fill_blank', 'default', 'Fill blank', 'Fallback prompt', '')
                """
            )
        )

    await create_schema(engine)

    async with engine.connect() as conn:
        columns = {
            str(row[1])
            for row in (await conn.execute(text("PRAGMA table_info('task')"))).fetchall()
        }
        rows = (await conn.execute(text("SELECT id, description FROM task ORDER BY id"))).fetchall()

    assert "prompt" not in columns
    assert dict(rows) == {
        "task_fill_blank": "Fallback prompt",
        "task_keep_description": "Canonical description",
    }
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


async def test_task_runtime_schema_upkeep_migrates_legacy_prompt_to_description(
    tmp_path: Path,
) -> None:
    """Task runtime upkeep should migrate prompt-only task tables before hot-path indexes."""

    db_path = tmp_path / "runtime_legacy_task_prompt.db"
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
                    'task_runtime_legacy', 'default', 'Runtime legacy task',
                    'Runtime legacy prompt body', 'todo', 'human', 'cli_user:alice',
                    'human', 'cli'
                )
                """
            )
        )

    await ensure_task_runtime_schema(engine)

    async with engine.connect() as conn:
        columns = {
            str(row[1])
            for row in (await conn.execute(text("PRAGMA table_info('task')"))).fetchall()
        }
        description = (
            await conn.execute(
                text("SELECT description FROM task WHERE id = 'task_runtime_legacy'")
            )
        ).scalar_one()

    assert "description" in columns
    assert "prompt" not in columns
    assert description == "Runtime legacy prompt body"
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


async def test_task_flow_create_task_reports_legacy_task_event_schema_mismatch(
    tmp_path: Path,
) -> None:
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
