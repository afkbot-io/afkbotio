"""Service-layer validation for managed runtime database posture."""

from __future__ import annotations

from pathlib import Path

from afkbot.db.dialect import database_driver_name
from afkbot.settings import Settings
from sqlalchemy.engine import make_url


class ManagedDatabaseGuardError(ValueError):
    """Raised when managed runtime database settings are unsafe."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def validate_managed_database_runtime(settings: Settings) -> None:
    """Validate database settings before starting a managed runtime.

    Managed containers keep state in a SQLite database inside the per-bot
    workspace volume. That keeps each bot self-contained across image versions
    and avoids coupling a runtime process to an external application database.

    :param settings: Resolved AFKBOT settings.
    :return: None.
    """

    if settings.deployment_mode != "managed":
        return
    driver_name = database_driver_name(settings.db_url)
    if not driver_name.startswith("sqlite"):
        raise ManagedDatabaseGuardError(
            error_code="managed_database_sqlite_required",
            reason="Managed runtime requires a SQLite database inside the workspace.",
        )
    db_path = _workspace_sqlite_path(settings=settings)
    workspace_root = Path(settings.root_dir).expanduser().resolve()
    if db_path.name == ":memory:" or db_path == workspace_root:
        raise ManagedDatabaseGuardError(
            error_code="managed_database_sqlite_file_required",
            reason="Managed runtime requires a SQLite file inside the workspace.",
        )
    try:
        db_path.relative_to(workspace_root)
    except ValueError as exc:
        raise ManagedDatabaseGuardError(
            error_code="managed_database_outside_workspace",
            reason="Managed runtime SQLite database must be inside the workspace.",
        ) from exc


def _workspace_sqlite_path(*, settings: Settings) -> Path:
    url = make_url(settings.db_url)
    database = str(url.database or "").strip()
    if not database:
        return Path(settings.root_dir).expanduser().resolve()
    path = Path(database).expanduser()
    if not path.is_absolute():
        path = Path(settings.root_dir) / path
    return path.resolve()
