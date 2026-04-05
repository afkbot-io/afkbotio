"""Shared helpers for resolving the local human owner/reviewer reference."""

from __future__ import annotations

import getpass

from afkbot.settings import Settings


def resolve_local_human_ref(settings: Settings) -> str:
    """Resolve the local human owner/reviewer reference used by CLI/chat flows."""

    explicit_ref = str(settings.chat_human_owner_ref or "").strip()
    if explicit_ref:
        return explicit_ref
    try:
        username = getpass.getuser().strip()
    except Exception:
        username = ""
    return f"cli_user:{username or 'local'}"
