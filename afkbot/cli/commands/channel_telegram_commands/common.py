"""Shared Telegram channel CLI constants and normalization helpers."""

from __future__ import annotations

from typing import cast

from afkbot.cli.commands.channel_shared import normalize_channel_choice_value
from afkbot.services.channels.endpoint_contracts import TelegramGroupTriggerMode

TELEGRAM_GROUP_TRIGGER_MODES = (
    "mention_or_reply",
    "reply_only",
    "mention_only",
    "all_messages",
)


def normalize_telegram_group_trigger_mode(value: str) -> TelegramGroupTriggerMode:
    """Normalize one Telegram group-trigger choice to its literal type."""

    return cast(TelegramGroupTriggerMode, normalize_channel_choice_value(value))


__all__ = ["TELEGRAM_GROUP_TRIGGER_MODES", "normalize_telegram_group_trigger_mode"]
