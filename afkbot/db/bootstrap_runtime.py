"""Runtime entrypoints for schema creation and database health checks."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.models import load_all_models
from afkbot.models.base import Base
from afkbot.services.automations.webhook_tokens import (
    hash_webhook_token,
    issue_webhook_token,
    stored_webhook_token_ref,
    stored_webhook_token_ref_hash,
)


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

    _ensure_task_runtime_columns(conn)
    _ensure_task_runtime_indexes(conn)
    _ensure_automation_delivery_columns(conn)
    _ensure_webhook_token_columns(conn)
    _ensure_webhook_execution_columns(conn)
    _backfill_webhook_token_hashes(conn)


def _ensure_task_runtime_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """Ensure newer Task Flow runtime columns exist for legacy SQLite installs."""

    columns = _table_columns(conn, "task")
    if not columns:
        return
    missing_columns = {
        "last_session_profile_id": "ALTER TABLE task ADD COLUMN last_session_profile_id VARCHAR(120)",
    }
    for column_name, ddl in missing_columns.items():
        if column_name not in columns:
            conn.execute(text(ddl))


def _ensure_task_runtime_indexes(conn) -> None:  # type: ignore[no-untyped-def]
    """Ensure Task Flow runtime indexes exist for legacy SQLite installs."""

    columns = _table_columns(conn, "task")
    if not columns:
        return
    duplicate_owner_scopes = _list_duplicate_active_ai_owner_scopes(conn)
    conn.execute(text("DROP INDEX IF EXISTS ux_task_active_ai_owner"))
    predicate = "owner_type = 'ai_profile' AND status IN ('claimed', 'running')"
    if duplicate_owner_scopes:
        excluded_owner_scopes = " AND ".join(
            "NOT (profile_id = "
            + _quote_sqlite_text_literal(profile_id)
            + " AND owner_ref = "
            + _quote_sqlite_text_literal(owner_ref)
            + ")"
            for profile_id, owner_ref in duplicate_owner_scopes
        )
        predicate = f"{predicate} AND {excluded_owner_scopes}"
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_task_active_ai_owner "
            "ON task (profile_id, owner_ref) "
            f"WHERE {predicate}"
        )
    )


def _list_duplicate_active_ai_owner_scopes(conn) -> tuple[tuple[str, str], ...]:  # type: ignore[no-untyped-def]
    """Return active AI owner profile scopes violating the one-active-task invariant."""

    if not _table_columns(conn, "task"):
        return ()
    rows = conn.execute(
        text(
            "SELECT profile_id, owner_ref "
            "FROM task "
            "WHERE owner_type = 'ai_profile' AND status IN ('claimed', 'running') "
            "GROUP BY profile_id, owner_ref "
            "HAVING COUNT(*) > 1 "
            "ORDER BY profile_id ASC, owner_ref ASC"
        )
    ).fetchall()
    scopes: list[tuple[str, str]] = []
    for profile_id, owner_ref in rows:
        profile_text = str(profile_id or "").strip()
        owner_text = str(owner_ref or "").strip()
        if profile_text and owner_text:
            scopes.append((profile_text, owner_text))
    return tuple(scopes)


def _quote_sqlite_text_literal(value: str) -> str:
    """Return one SQL-safe SQLite string literal."""

    return "'" + str(value).replace("'", "''") + "'"


def _ensure_automation_delivery_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """Ensure newer automation delivery columns exist for legacy SQLite installs."""

    columns = _table_columns(conn, "automation")
    if not columns:
        return
    if "delivery_mode" not in columns:
        conn.execute(
            text(
                "ALTER TABLE automation "
                "ADD COLUMN delivery_mode VARCHAR(16) NOT NULL DEFAULT 'tool'"
            )
        )
    if "delivery_target_json" not in columns:
        conn.execute(text("ALTER TABLE automation ADD COLUMN delivery_target_json TEXT"))
    conn.execute(
        text(
            "UPDATE automation "
            "SET delivery_mode = 'tool' "
            "WHERE delivery_mode IS NULL OR delivery_mode = ''"
        )
    )


def _ensure_webhook_token_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """Ensure webhook token hash/ref columns and indexes exist for older installations."""

    columns = _table_columns(conn, "automation_trigger_webhook")
    if not columns:
        return
    if "webhook_token" not in columns:
        conn.execute(
            text("ALTER TABLE automation_trigger_webhook ADD COLUMN webhook_token VARCHAR(255)")
        )
    if "webhook_token_hash" not in columns:
        conn.execute(
            text(
                "ALTER TABLE automation_trigger_webhook ADD COLUMN webhook_token_hash VARCHAR(128)"
            )
        )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_automation_webhook_token "
            "ON automation_trigger_webhook (webhook_token)"
        )
    )
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_automation_webhook_token_hash "
            "ON automation_trigger_webhook (webhook_token_hash)"
        )
    )


def _ensure_webhook_execution_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """Ensure webhook execution status columns exist for older installations."""

    columns = _table_columns(conn, "automation_trigger_webhook")
    if not columns:
        return
    missing_columns = {
        "last_session_id": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_session_id VARCHAR(255)",
        "last_started_at": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_started_at DATETIME",
        "last_succeeded_at": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_succeeded_at DATETIME",
        "last_failed_at": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_failed_at DATETIME",
        "last_error": "ALTER TABLE automation_trigger_webhook ADD COLUMN last_error TEXT",
    }
    for column_name, ddl in missing_columns.items():
        if column_name not in columns:
            conn.execute(text(ddl))


def _backfill_webhook_token_hashes(conn) -> None:  # type: ignore[no-untyped-def]
    """Hash legacy plaintext webhook tokens and replace them with non-bearer refs."""

    columns = _table_columns(conn, "automation_trigger_webhook")
    if "webhook_token" not in columns or "webhook_token_hash" not in columns:
        return

    webhook_rows = conn.execute(
        text(
            "SELECT automation_id, webhook_token, webhook_token_hash "
            "FROM automation_trigger_webhook"
        )
    ).fetchall()
    if not webhook_rows:
        return

    existing_hashes = {
        str(row[0])
        for row in conn.execute(
            text(
                "SELECT webhook_token_hash FROM automation_trigger_webhook "
                "WHERE webhook_token_hash IS NOT NULL AND webhook_token_hash != ''"
            )
        ).fetchall()
    }

    for automation_id, stored_token, stored_hash in webhook_rows:
        token_ref = str(stored_token or "").strip()
        token_hash = str(stored_hash or "").strip()
        if not token_hash:
            token_hash = stored_webhook_token_ref_hash(token_ref) or (
                hash_webhook_token(token_ref) if token_ref else ""
            )
        if not token_hash:
            token_hash = _issue_unique_webhook_token_hash(existing_hashes=existing_hashes)
        existing_hashes.add(token_hash)
        next_token_ref = stored_webhook_token_ref(token_hash)
        if token_ref == next_token_ref and str(stored_hash or "").strip() == token_hash:
            continue
        conn.execute(
            text(
                "UPDATE automation_trigger_webhook "
                "SET webhook_token = :token_ref, webhook_token_hash = :token_hash "
                "WHERE automation_id = :automation_id"
            ),
            {
                "automation_id": int(automation_id),
                "token_ref": next_token_ref,
                "token_hash": token_hash,
            },
        )


def _issue_unique_webhook_token_hash(*, existing_hashes: set[str]) -> str:
    """Issue one webhook token hash not present in current hash set."""

    while True:
        token = issue_webhook_token()
        token_hash = hash_webhook_token(token)
        if token_hash not in existing_hashes:
            existing_hashes.add(token_hash)
            return token_hash


def _table_columns(conn, table_name: str) -> set[str]:  # type: ignore[no-untyped-def]
    """Return current column names for one SQLite table."""

    rows = conn.execute(text(f"PRAGMA table_info('{table_name}')")).fetchall()
    return {str(row[1]) for row in rows}
