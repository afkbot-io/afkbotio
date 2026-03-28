"""Compatibility facade for memory CLI command registration."""

from __future__ import annotations

import typer

from afkbot.cli.commands.memory_read_commands import register_memory_read_commands
from afkbot.cli.commands.memory_write_commands import register_memory_write_commands


def register(app: typer.Typer) -> None:
    """Register `afk memory ...` commands."""

    memory_app = typer.Typer(
        help="Manage scoped semantic memory items.",
        no_args_is_help=True,
    )
    app.add_typer(memory_app, name="memory")
    register_memory_read_commands(memory_app)
    register_memory_write_commands(memory_app)


__all__ = ["register"]
