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
        prompt="Keep this active after upgrade normalization.",
        created_by_type="human",
        created_by_ref="cli",
        owner_type="ai_profile",
        owner_ref="analyst",
    )
    second = await service.create_task(
        profile_id="default",
        title="Legacy duplicate analyst task",
        prompt="This should be released during schema normalization.",
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
    assert "owner_ref NOT IN ('analyst')" in str(index_sql)
    assert first_after.status == "running"
    assert second_after.status == "claimed"
    assert second_after.last_error_code is None
    await engine.dispose()
