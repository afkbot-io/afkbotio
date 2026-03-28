"""Compatibility exports for Telegram channel mutation helpers."""

from afkbot.cli.commands.channel_telegram_commands.mutations_add import run_telegram_add
from afkbot.cli.commands.channel_telegram_commands.mutations_update import run_telegram_update

__all__ = ["run_telegram_add", "run_telegram_update"]
