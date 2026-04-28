"""Shared constants and normalizers for Telethon channel CLI commands."""

from __future__ import annotations

from typing import cast

from afkbot.cli.commands.channel_shared import normalize_channel_choice_value
from afkbot.services.channels.endpoint_contracts import (
    TelethonGroupInvocationMode,
    TelethonReplyMode,
)

TELETHON_REPLY_MODES = ("disabled", "same_chat")
TELETHON_REPLY_MODE_LABEL_OVERRIDES = {
    "disabled": (
        "disabled - read incoming messages only, do not send replies",
        "disabled - только читать входящие сообщения, не отправлять ответы",
    ),
}
TELETHON_GROUP_INVOCATION_MODES = (
    "reply_or_command",
    "reply_only",
    "command_only",
    "all_messages",
)


def normalize_telethon_reply_mode(value: str) -> TelethonReplyMode:
    """Normalize one Telethon reply-mode choice to its literal type."""

    return cast(TelethonReplyMode, normalize_channel_choice_value(value))


def normalize_telethon_group_invocation_mode(value: str) -> TelethonGroupInvocationMode:
    """Normalize one Telethon group-invocation choice to its literal type."""

    return cast(TelethonGroupInvocationMode, normalize_channel_choice_value(value))


def split_csv_patterns(raw: str | None) -> tuple[str, ...]:
    """Split one CLI CSV flag into trimmed pattern values."""

    if raw is None:
        return ()
    return tuple(
        part.strip()
        for part in raw.split(",")
        if part.strip()
    )


__all__ = [
    "TELETHON_GROUP_INVOCATION_MODES",
    "TELETHON_REPLY_MODE_LABEL_OVERRIDES",
    "TELETHON_REPLY_MODES",
    "normalize_telethon_group_invocation_mode",
    "normalize_telethon_reply_mode",
    "split_csv_patterns",
]
