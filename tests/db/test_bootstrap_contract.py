"""Contract tests for the clean SQLite bootstrap baseline."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from afkbot.db.bootstrap import create_schema, list_applied_migrations, ping
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.repositories.profile_repo import ProfileRepository
from afkbot.settings import Settings
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
    assert int(foreign_keys_enabled) == 1
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
    assert "ON task (profile_id, owner_ref)" in index_sql_text
    assert "NOT (profile_id = 'default' AND owner_ref = 'analyst')" in index_sql_text
    assert first_after.status == "running"
    assert second_after.status == "claimed"
    assert second_after.last_error_code is None
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
