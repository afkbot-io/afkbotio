"""Service-layer validation for managed runtime database posture."""

from __future__ import annotations

from afkbot.db.dialect import database_driver_name
from afkbot.settings import Settings


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
