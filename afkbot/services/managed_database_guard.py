"""Service-layer validation for managed runtime database posture."""

from __future__ import annotations

from afkbot.db.postgres_contract import (
    PostgresBootstrapContractError,
    validate_managed_postgres_settings,
)
from afkbot.settings import Settings

ManagedDatabaseGuardError = PostgresBootstrapContractError


def validate_managed_database_runtime(settings: Settings) -> None:
    """Validate database settings before starting a managed runtime."""

    validate_managed_postgres_settings(settings)
