"""Helpers for removing AFKBOT-owned project data during uninstall."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.engine import make_url

from afkbot.settings import Settings


def clear_profiles_workspace(settings: Settings) -> None:
    """Remove the AFKBOT profiles workspace when it is safely scoped to root."""

    root_dir = settings.root_dir.resolve(strict=False)
    profiles_dir = settings.profiles_dir.resolve(strict=False)
    if profiles_dir == root_dir:
        raise ValueError("refusing to remove profiles workspace equal to root directory")
    if not profiles_dir.is_relative_to(root_dir):
        raise ValueError("refusing to remove profiles workspace outside runtime root")
    if profiles_dir.exists():
        shutil.rmtree(profiles_dir)


def clear_local_sqlite_db(settings: Settings) -> None:
    """Remove the local SQLite database file when it is scoped to the runtime root."""

    try:
        normalized_db_url = Settings(
            root_dir=settings.root_dir,
            db_url=settings.db_url,
        ).db_url
        url = make_url(normalized_db_url)
    except Exception:
        return
    if not str(url.drivername).startswith("sqlite"):
        return
    database = str(url.database or "").strip()
    if not database or database == ":memory:":
        return
    resolved_root = settings.root_dir.resolve(strict=False)
    resolved_target = Path(database).expanduser().resolve(strict=False)
    if not resolved_target.is_relative_to(resolved_root):
        raise ValueError("refusing to remove SQLite database outside runtime root")
    for candidate_path in _sqlite_cleanup_paths(resolved_target):
        if candidate_path.exists():
            candidate_path.unlink()


def _sqlite_cleanup_paths(database_path: Path) -> tuple[Path, ...]:
    """Return SQLite database paths that should be removed on uninstall."""

    return (
        database_path,
        database_path.with_name(f"{database_path.name}-wal"),
        database_path.with_name(f"{database_path.name}-shm"),
    )
