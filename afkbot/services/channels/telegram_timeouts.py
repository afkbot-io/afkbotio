"""Shared Telegram Bot API timeout helpers for channel runtimes."""

from __future__ import annotations

TELEGRAM_ACTION_TIMEOUT_PREFIX = "Telegram action timed out after "


def is_telegram_action_timeout_reason(reason: str) -> bool:
    """Return whether one error reason represents a Telegram action timeout."""

    return reason.startswith(TELEGRAM_ACTION_TIMEOUT_PREFIX)
