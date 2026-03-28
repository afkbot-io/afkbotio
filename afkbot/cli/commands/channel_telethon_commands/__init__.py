"""Telethon channel CLI package exports."""

from afkbot.cli.commands.channel_telethon_commands.app import (
    register_telethon_command_tree,
    register_telethon_commands,
)

__all__ = ["register_telethon_command_tree", "register_telethon_commands"]
