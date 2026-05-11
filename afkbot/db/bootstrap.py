"""Public facade for database bootstrap entrypoints."""

from __future__ import annotations

from afkbot.db.bootstrap_runtime import ManagedRuntimeSchemaError, create_schema, list_applied_migrations, ping
from afkbot.db.postgres_contract import (
    PostgresBootstrapContractError,
    PostgresBootstrapPlan,
    PostgresDatabasePerBotContract,
    build_database_per_bot_contract,
    render_database_per_bot_bootstrap_plan,
    validate_managed_postgres_settings,
)

__all__ = [
    "create_schema",
    "list_applied_migrations",
    "ping",
    "ManagedRuntimeSchemaError",
    "PostgresBootstrapContractError",
    "PostgresBootstrapPlan",
    "PostgresDatabasePerBotContract",
    "build_database_per_bot_contract",
    "render_database_per_bot_bootstrap_plan",
    "validate_managed_postgres_settings",
]
