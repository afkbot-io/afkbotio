"""Telegram channel CLI command helpers."""

from afkbot.cli.commands.channel_telegram_commands.common import (
    TELEGRAM_GROUP_TRIGGER_MODES,
    normalize_telegram_group_trigger_mode,
)
from afkbot.cli.commands.channel_telegram_commands.registration import (
    register_telegram_command_tree,
)
from afkbot.cli.commands.channel_telegram_commands.runtime import (
    TelegramCommandRuntime,
    TelegramPayload,
)
from afkbot.cli.commands.channel_telegram_commands.mutations import (
    run_telegram_add,
    run_telegram_update,
)

__all__ = [
    "TELEGRAM_GROUP_TRIGGER_MODES",
    "TelegramCommandRuntime",
    "TelegramPayload",
    "normalize_telegram_group_trigger_mode",
    "register_telegram_command_tree",
    "run_telegram_add",
    "run_telegram_update",
]
