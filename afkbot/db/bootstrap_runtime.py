"""Runtime entrypoints for schema creation and database health checks."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.models import load_all_models
from afkbot.models.base import Base
from afkbot.services.automations.webhook_tokens import hash_webhook_token, issue_webhook_token


async def create_schema(engine: AsyncEngine) -> None:
    """Create all mapped tables for the tracked SQLite runtime."""

    load_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_upgrade_schema)


async def list_applied_migrations(engine: AsyncEngine) -> tuple[int, ...]:
    """Return applied schema migrations for the clean SQLite baseline."""

    del engine
    return ()


async def ping(engine: AsyncEngine) -> bool:
    """Return True when the database is reachable."""

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        return int(result.scalar_one()) == 1


def _upgrade_schema(conn) -> None:  # type: ignore[no-untyped-def]
    """Apply lightweight idempotent schema upgrades for existing SQLite databases."""

    _ensure_webhook_token_column(conn)
    _ensure_webhook_execution_columns(conn)
    _backfill_missing_webhook_tokens(conn)


def _ensure_webhook_token_column(conn) -> None:  # type: ignore[no-untyped-def]
    """Ensure webhook plaintext token column/index exist for older installations."""

    columns = _table_columns(conn, "automation_trigger_webhook")
    if not columns:
        return
    if "webhook_token" not in columns:
        conn.execute(
            text(
                "ALTER TABLE automation_trigger_webhook "
                "ADD COLUMN webhook_token VARCHAR(255)"
            )
        )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_automation_webhook_token "
            "ON automation_trigger_webhook (webhook_token)"
        )
    )


def _ensure_webhook_execution_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """Ensure webhook execution status columns exist for older installations."""

    columns = _table_columns(conn, "automation_trigger_webhook")
    if not columns:
        return
    missing_columns = {
        "last_session_id": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_session_id VARCHAR(255)",
        "last_succeeded_at": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_succeeded_at DATETIME",
        "last_failed_at": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_failed_at DATETIME",
        "last_error": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_error TEXT",
    }
    for column_name, ddl in missing_columns.items():
        if column_name not in columns:
            conn.execute(text(ddl))


def _backfill_missing_webhook_tokens(conn) -> None:  # type: ignore[no-untyped-def]
    """Issue replacement tokens for legacy webhook rows that never stored plaintext tokens."""

    columns = _table_columns(conn, "automation_trigger_webhook")
    if "webhook_token" not in columns:
        return

    missing_rows = conn.execute(
        text(
            "SELECT automation_id FROM automation_trigger_webhook "
            "WHERE webhook_token IS NULL OR webhook_token = ''"
        )
    ).fetchall()
    if not missing_rows:
        return

    existing_tokens = {
        str(row[0])
        for row in conn.execute(
            text(
                "SELECT webhook_token FROM automation_trigger_webhook "
                "WHERE webhook_token IS NOT NULL AND webhook_token != ''"
            )
        ).fetchall()
    }
    existing_hashes = {
        str(row[0])
        for row in conn.execute(
            text(
                "SELECT webhook_token_hash FROM automation_trigger_webhook "
                "WHERE webhook_token_hash IS NOT NULL AND webhook_token_hash != ''"
            )
        ).fetchall()
    }

    for automation_id, in missing_rows:
        token, token_hash = _issue_unique_webhook_token(
            existing_tokens=existing_tokens,
            existing_hashes=existing_hashes,
        )
        conn.execute(
            text(
                "UPDATE automation_trigger_webhook "
                "SET webhook_token = :token, webhook_token_hash = :token_hash "
                "WHERE automation_id = :automation_id"
            ),
            {
                "automation_id": int(automation_id),
                "token": token,
                "token_hash": token_hash,
            },
        )


def _issue_unique_webhook_token(
    *,
    existing_tokens: set[str],
    existing_hashes: set[str],
) -> tuple[str, str]:
    """Issue one webhook token not present in current plaintext/hash sets."""

    while True:
        token = issue_webhook_token()
        token_hash = hash_webhook_token(token)
        if token not in existing_tokens and token_hash not in existing_hashes:
            existing_tokens.add(token)
            existing_hashes.add(token_hash)
            return token, token_hash


def _table_columns(conn, table_name: str) -> set[str]:  # type: ignore[no-untyped-def]
    """Return current column names for one SQLite table."""

    rows = conn.execute(text(f"PRAGMA table_info('{table_name}')")).fetchall()
    return {str(row[1]) for row in rows}
