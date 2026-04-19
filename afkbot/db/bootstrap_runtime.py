"""Runtime entrypoints for schema creation, upkeep, and bounded history cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import MetaData, Table, delete, exists, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from afkbot.models import load_all_models
from afkbot.models.base import Base
from afkbot.models.task import Task
from afkbot.services.automations.webhook_tokens import (
    hash_webhook_token,
    issue_webhook_token,
    stored_webhook_token_ref,
    stored_webhook_token_ref_hash,
)


@dataclass(frozen=True, slots=True)
class RuntimeHistoryPruneResult:
    """Bounded row counts deleted from append-only runtime history tables."""

    task_event_count: int = 0
    task_run_count: int = 0
    runlog_event_count: int = 0


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


async def ensure_task_runtime_schema(engine: AsyncEngine) -> None:
    """Refresh Task Flow runtime upkeep without re-running full global bootstrap."""

    load_all_models()
    async with engine.begin() as conn:
        await conn.run_sync(_upgrade_task_runtime_schema)


async def prune_runtime_history(
    engine: AsyncEngine,
    *,
    task_event_before: datetime | None = None,
    task_run_before: datetime | None = None,
    runlog_event_before: datetime | None = None,
    batch_size: int = 500,
) -> RuntimeHistoryPruneResult:
    """Prune bounded batches from append-only runtime history tables."""

    normalized_batch_size = int(batch_size)
    if normalized_batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    load_all_models()
    async with engine.begin() as conn:
        return await conn.run_sync(
            _prune_runtime_history_sync,
            task_event_before=task_event_before,
            task_run_before=task_run_before,
            runlog_event_before=runlog_event_before,
            batch_size=normalized_batch_size,
        )


def _upgrade_schema(conn: Connection) -> None:
    """Apply lightweight idempotent schema upgrades for existing SQLite databases."""

    _ensure_task_description_column(conn)
    _ensure_run_indexes(conn)
    _ensure_task_runtime_columns(conn)
    _ensure_task_runtime_indexes(conn)
    _ensure_runtime_history_indexes(conn)
    _ensure_automation_delivery_columns(conn)
    _ensure_automation_runtime_columns(conn)
    _ensure_automation_graph_runtime_columns(conn)
    _ensure_webhook_token_columns(conn)
    _ensure_webhook_execution_columns(conn)
    _backfill_webhook_token_hashes(conn)


def _upgrade_task_runtime_schema(conn: Connection) -> None:
    """Apply only Task Flow runtime upkeep needed on execution hot paths."""

    _ensure_task_runtime_columns(conn)
    _ensure_task_runtime_indexes(conn)
    _ensure_runtime_history_indexes(conn)


def _ensure_task_description_column(conn: Connection) -> None:
    """Ensure legacy task tables gain the description column backed by prompt text."""

    columns = _table_columns(conn, "task")
    if not columns:
        return
    if "prompt" in columns:
        _rebuild_legacy_task_table(conn, legacy_columns=columns)
        return
    if "description" not in columns:
        conn.execute(text("ALTER TABLE task ADD COLUMN description TEXT"))


def _rebuild_legacy_task_table(
    conn: Connection,
    *,
    legacy_columns: set[str],
) -> None:
    """Rebuild the legacy task table so inserts no longer depend on prompt."""

    temp_table_name = "task__rebuilt_description"
    conn.execute(text("PRAGMA foreign_keys=OFF"))
    try:
        conn.execute(text(f"DROP TABLE IF EXISTS {temp_table_name}"))
        rebuilt_metadata = MetaData()
        task_table = cast(Table, Task.__table__)
        for table_name in ("profile", "task_flow"):
            table = Base.metadata.tables.get(table_name)
            if table is not None:
                table.to_metadata(rebuilt_metadata)
        rebuilt_task = task_table.to_metadata(rebuilt_metadata, name=temp_table_name)
        for index in tuple(rebuilt_task.indexes):
            rebuilt_task.indexes.discard(index)
        rebuilt_task.create(bind=conn)

        insert_columns = [_quote_sqlite_identifier(column.name) for column in task_table.columns]
        select_columns = [
            _legacy_task_select_expression(column_name=column.name, legacy_columns=legacy_columns)
            for column in task_table.columns
        ]
        conn.execute(
            text(
                f"INSERT INTO {temp_table_name} ({', '.join(insert_columns)}) "
                f"SELECT {', '.join(select_columns)} FROM task"
            )
        )
        conn.execute(text("DROP TABLE task"))
        conn.execute(text(f"ALTER TABLE {temp_table_name} RENAME TO task"))
        for index in task_table.indexes:
            index.create(bind=conn, checkfirst=True)
    finally:
        conn.execute(text("PRAGMA foreign_keys=ON"))


def _legacy_task_select_expression(*, column_name: str, legacy_columns: set[str]) -> str:
    """Build one SELECT expression for copying rows out of a legacy task table."""

    if column_name == "description":
        if "description" in legacy_columns and "prompt" in legacy_columns:
            return "COALESCE(NULLIF(description, ''), prompt)"
        if "description" in legacy_columns:
            return "description"
        if "prompt" in legacy_columns:
            return "prompt"
        return "''"
    if column_name in legacy_columns:
        return _quote_sqlite_identifier(column_name)
    default_by_column = {
        "status": "'todo'",
        "priority": "50",
        "owner_type": "'human'",
        "owner_ref": "''",
        "source_type": "'manual'",
        "created_by_type": "'human'",
        "created_by_ref": "''",
        "labels_json": "'[]'",
        "requires_review": "0",
        "current_attempt": "0",
        "created_at": "CURRENT_TIMESTAMP",
        "updated_at": "CURRENT_TIMESTAMP",
    }
    return default_by_column.get(column_name, "NULL")


def _ensure_task_runtime_columns(conn: Connection) -> None:
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


def _ensure_run_indexes(conn: Connection) -> None:
    """Ensure run hot-path indexes exist for latest-run and cancel lookups."""

    if not _table_columns(conn, "run"):
        return
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_run_profile_session_id "
            "ON run (profile_id, session_id, id)"
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_run_profile_session_status_id "
            "ON run (profile_id, session_id, status, id)"
        )
    )


def _ensure_task_runtime_indexes(conn: Connection) -> None:
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


def _ensure_runtime_history_indexes(conn: Connection) -> None:
    """Ensure retention-friendly indexes exist for append-only runtime history."""

    if _table_columns(conn, "task"):
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_task_last_run_id "
                "ON task (last_run_id)"
            )
        )
    if _table_columns(conn, "task_event"):
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_task_event_created_at "
                "ON task_event (created_at, id)"
            )
        )
    if _table_columns(conn, "task_run"):
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_task_run_finished_at "
                "ON task_run (finished_at, id)"
            )
        )
    if _table_columns(conn, "runlog_event"):
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_runlog_event_created_at "
                "ON runlog_event (created_at, id)"
            )
        )
def _list_duplicate_active_ai_owner_scopes(conn: Connection) -> tuple[tuple[str, str], ...]:
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


def _quote_sqlite_identifier(value: str) -> str:
    """Return one SQL-safe SQLite identifier."""

    return '"' + str(value).replace('"', '""') + '"'


def _ensure_automation_runtime_columns(conn: Connection) -> None:
    """Ensure newer automation runtime columns exist for legacy SQLite installs."""

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
    if "execution_mode" not in columns:
        conn.execute(
            text(
                "ALTER TABLE automation "
                "ADD COLUMN execution_mode VARCHAR(16) NOT NULL DEFAULT 'prompt'"
            )
        )
    if "graph_fallback_mode" not in columns:
        conn.execute(
            text(
                "ALTER TABLE automation "
                "ADD COLUMN graph_fallback_mode VARCHAR(32) "
                "NOT NULL DEFAULT 'resume_with_ai_if_safe'"
            )
        )
    conn.execute(
        text(
            "UPDATE automation "
            "SET delivery_mode = 'tool' "
            "WHERE delivery_mode IS NULL OR delivery_mode = ''"
        )
    )
    conn.execute(
        text(
            "UPDATE automation "
            "SET execution_mode = 'prompt' "
            "WHERE execution_mode IS NULL OR execution_mode = ''"
        )
    )
    conn.execute(
        text(
            "UPDATE automation "
            "SET graph_fallback_mode = 'resume_with_ai_if_safe' "
            "WHERE graph_fallback_mode IS NULL OR graph_fallback_mode = ''"
        )
    )


def _ensure_automation_graph_runtime_columns(conn: Connection) -> None:
    """Ensure newer graph ledger columns exist for legacy SQLite installs."""

    columns = _table_columns(conn, "automation_node_run")
    if not columns:
        return
    if "execution_index" not in columns:
        conn.execute(text("ALTER TABLE automation_node_run ADD COLUMN execution_index INTEGER"))
    if "effects_json" not in columns:
        conn.execute(text("ALTER TABLE automation_node_run ADD COLUMN effects_json TEXT"))


def _ensure_webhook_token_columns(conn: Connection) -> None:
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


def _ensure_webhook_execution_columns(conn: Connection) -> None:
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


def _backfill_webhook_token_hashes(conn: Connection) -> None:
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


def _table_columns(conn: Connection, table_name: str) -> set[str]:
    """Return current column names for one SQLite table."""

    rows = conn.execute(text(f"PRAGMA table_info('{table_name}')")).fetchall()
    return {str(row[1]) for row in rows}


def _prune_runtime_history_sync(  # type: ignore[no-untyped-def]
    conn,
    *,
    task_event_before,
    task_run_before,
    runlog_event_before,
    batch_size: int,
) -> RuntimeHistoryPruneResult:
    metadata = Base.metadata
    task_table = metadata.tables["task"]
    task_event_table = metadata.tables["task_event"]
    task_run_table = metadata.tables["task_run"]
    runlog_event_table = metadata.tables["runlog_event"]

    task_event_count = 0
    if task_event_before is not None:
        task_event_ids = (
            select(task_event_table.c.id)
            .where(task_event_table.c.created_at < task_event_before)
            .order_by(task_event_table.c.created_at.asc(), task_event_table.c.id.asc())
            .limit(batch_size)
        )
        task_event_count = conn.execute(
            delete(task_event_table).where(task_event_table.c.id.in_(task_event_ids))
        ).rowcount or 0

    task_run_count = 0
    if task_run_before is not None:
        task_run_ids = (
            select(task_run_table.c.id)
            .where(task_run_table.c.finished_at.is_not(None))
            .where(task_run_table.c.finished_at < task_run_before)
            .where(
                ~exists(select(task_table.c.id).where(task_table.c.last_run_id == task_run_table.c.id))
            )
            .where(
                ~exists(
                    select(task_event_table.c.id).where(task_event_table.c.task_run_id == task_run_table.c.id)
                )
            )
            .order_by(task_run_table.c.finished_at.asc(), task_run_table.c.id.asc())
            .limit(batch_size)
        )
        task_run_count = conn.execute(
            delete(task_run_table).where(task_run_table.c.id.in_(task_run_ids))
        ).rowcount or 0

    runlog_event_count = 0
    if runlog_event_before is not None:
        runlog_event_ids = (
            select(runlog_event_table.c.id)
            .where(runlog_event_table.c.created_at < runlog_event_before)
            .order_by(runlog_event_table.c.created_at.asc(), runlog_event_table.c.id.asc())
            .limit(batch_size)
        )
        runlog_event_count = conn.execute(
            delete(runlog_event_table).where(runlog_event_table.c.id.in_(runlog_event_ids))
        ).rowcount or 0

    return RuntimeHistoryPruneResult(
        task_event_count=task_event_count,
        task_run_count=task_run_count,
        runlog_event_count=runlog_event_count,
    )
