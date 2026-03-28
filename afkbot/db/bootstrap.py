"""Public facade for database bootstrap entrypoints."""

from __future__ import annotations

from afkbot.db.bootstrap_runtime import create_schema, list_applied_migrations, ping

__all__ = [
    "create_schema",
    "list_applied_migrations",
    "ping",
]
