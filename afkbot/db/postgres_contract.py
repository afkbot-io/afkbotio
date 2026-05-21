"""PostgreSQL database-per-bot bootstrap contract for managed runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import re

_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class PostgresBootstrapContractError(ValueError):
    """Raised when a managed Postgres bootstrap contract is unsafe."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class PostgresDatabasePerBotContract:
    """Validated database-per-bot allocation contract."""

    bot_id: str
    database_name: str
    migrator_role_name: str
    runtime_role_name: str
    app_schema: str = "public"


@dataclass(frozen=True, slots=True)
class PostgresBootstrapPlan:
    """SQL contract split between cluster-admin and bot-database phases."""

    cluster_statements: tuple[str, ...]
    migration_statements: tuple[str, ...]
    database_statements: tuple[str, ...]


def build_database_per_bot_contract(
    *,
    bot_id: str,
    database_name: str | None = None,
    migrator_role_name: str | None = None,
    runtime_role_name: str | None = None,
    app_schema: str = "public",
) -> PostgresDatabasePerBotContract:
    """Build the default managed Postgres contract for one bot."""

    normalized_bot_id = _normalize_bot_id(bot_id)
    contract = PostgresDatabasePerBotContract(
        bot_id=normalized_bot_id,
        database_name=database_name or f"afkbot_{normalized_bot_id}",
        migrator_role_name=migrator_role_name or f"afkbot_{normalized_bot_id}_migrator",
        runtime_role_name=runtime_role_name or f"afkbot_{normalized_bot_id}_runtime",
        app_schema=app_schema,
    )
    _validate_identifier(contract.database_name, field="database_name")
    _validate_identifier(contract.migrator_role_name, field="migrator_role_name")
    _validate_identifier(contract.runtime_role_name, field="runtime_role_name")
    _validate_identifier(contract.app_schema, field="app_schema")
    return contract


def render_database_per_bot_bootstrap_plan(
    contract: PostgresDatabasePerBotContract,
) -> PostgresBootstrapPlan:
    """Render the SQL shape expected from the external control-plane bootstrapper."""

    database = _quote_identifier(contract.database_name)
    migrator_role = _quote_identifier(contract.migrator_role_name)
    runtime_role = _quote_identifier(contract.runtime_role_name)
    schema = _quote_identifier(contract.app_schema)
    return PostgresBootstrapPlan(
        cluster_statements=(
            f"CREATE ROLE {migrator_role} LOGIN PASSWORD :migrator_role_password NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT",
            f"CREATE ROLE {runtime_role} LOGIN PASSWORD :runtime_role_password NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT",
            f"CREATE DATABASE {database} OWNER {migrator_role} TEMPLATE template0 ENCODING 'UTF8'",
            f"GRANT CONNECT ON DATABASE {database} TO {runtime_role}",
        ),
        migration_statements=(
            "SELECT pg_advisory_lock(hashtext(current_database()))",
            "CREATE TABLE IF NOT EXISTS afkbot_schema_migration ("
            "version INTEGER PRIMARY KEY, "
            "description TEXT NOT NULL, "
            "checksum TEXT NOT NULL DEFAULT '', "
            "applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")",
            "SELECT pg_advisory_unlock(hashtext(current_database()))",
        ),
        database_statements=(
            "REVOKE ALL ON SCHEMA public FROM PUBLIC",
            f"REVOKE CREATE ON SCHEMA {schema} FROM PUBLIC",
            f"ALTER SCHEMA {schema} OWNER TO {migrator_role}",
            f"GRANT USAGE ON SCHEMA {schema} TO {runtime_role}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator_role} IN SCHEMA {schema} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {runtime_role}",
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {migrator_role} IN SCHEMA {schema} GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO {runtime_role}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {schema} TO {runtime_role}",
            f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA {schema} TO {runtime_role}",
        ),
    )


def _normalize_bot_id(bot_id: str) -> str:
    normalized = str(bot_id or "").strip().lower().replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]", "_", normalized)
    normalized = normalized.strip("_")
    if not normalized:
        raise PostgresBootstrapContractError(
            error_code="postgres_bot_id_required",
            reason="bot_id is required to build a database-per-bot contract.",
        )
    if normalized[0].isdigit():
        normalized = f"bot_{normalized}"
    return normalized[:48]


def _validate_identifier(value: str, *, field: str) -> None:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise PostgresBootstrapContractError(
            error_code="postgres_identifier_invalid",
            reason=f"{field} must be a safe PostgreSQL identifier.",
        )


def _quote_identifier(value: str) -> str:
    _validate_identifier(value, field="identifier")
    return '"' + value.replace('"', '""') + '"'
