"""Typer app assembly helpers for Telethon channel CLI commands."""

from __future__ import annotations

import typer

from afkbot.cli.commands.channel_telethon_commands.add_command import (
    register_telethon_add_command,
)
from afkbot.cli.commands.channel_telethon_commands.queries import (
    register_telethon_query_commands,
)
from afkbot.cli.commands.channel_telethon_commands.update_command import (
    register_telethon_update_command,
)


def register_telethon_command_tree(channel_app: typer.Typer) -> None:
    """Register the Telethon command tree under `afk channel telethon`."""

    telethon_app = typer.Typer(
        help="Telethon user-account channel controls.",
        no_args_is_help=True,
    )
    register_telethon_add_command(telethon_app)
    register_telethon_update_command(telethon_app)
    register_telethon_query_commands(telethon_app)
    channel_app.add_typer(telethon_app, name="telethon")


register_telethon_commands = register_telethon_command_tree


__all__ = ["register_telethon_command_tree", "register_telethon_commands"]
