"""Tests for uninstall-time runtime root cleanup helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from afkbot.services.setup.project_cleanup import clear_local_sqlite_db
from afkbot.settings import Settings


def test_clear_local_sqlite_db_removes_relative_runtime_db_under_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup should resolve relative SQLite URLs the same way as runtime settings."""

    # Arrange
    runtime_root = tmp_path / "runtime-root"
    other_cwd = tmp_path / "other-cwd"
    runtime_root.mkdir()
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    db_path = runtime_root / "afkbot.db"
    db_path.write_text("db", encoding="utf-8")
    wal_path = runtime_root / "afkbot.db-wal"
    wal_path.write_text("wal", encoding="utf-8")
    shm_path = runtime_root / "afkbot.db-shm"
    shm_path.write_text("shm", encoding="utf-8")
    settings = Settings(
        root_dir=runtime_root,
        db_url="sqlite+aiosqlite:///./afkbot.db",
    )

    # Act
    clear_local_sqlite_db(settings)

    # Assert
    assert not db_path.exists()
    assert not wal_path.exists()
    assert not shm_path.exists()


def test_clear_local_sqlite_db_rejects_paths_outside_runtime_root(tmp_path: Path) -> None:
    """Cleanup should fail closed when the SQLite database is outside runtime root."""

    # Arrange
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir()
    outside_db = tmp_path / "outside.db"
    outside_db.write_text("db", encoding="utf-8")
    settings = Settings(
        root_dir=runtime_root,
        db_url=f"sqlite+aiosqlite:///{outside_db}",
    )

    # Act / Assert
    with pytest.raises(ValueError, match="outside runtime root"):
        clear_local_sqlite_db(settings)
