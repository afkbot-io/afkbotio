"""Contract tests for the clean SQLite bootstrap baseline."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from afkbot.db.bootstrap import create_schema, list_applied_migrations, ping
from afkbot.db.engine import create_engine
from afkbot.settings import Settings


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
