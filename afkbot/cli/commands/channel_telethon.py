"""Compatibility facade for Telethon channel CLI commands."""

from __future__ import annotations

import typer

from afkbot.cli.managed_runtime import reload_install_managed_runtime_notice
from afkbot.cli.commands.channel_telethon_commands import register_telethon_command_tree
from afkbot.cli.commands.channel_telethon_runtime import (
    load_telethon_endpoint,
    raise_telethon_channel_error,
    render_telethon_status_payload,
    set_telethon_endpoint_enabled,
    telethon_authorize_payload,
    telethon_dialogs_payload,
    telethon_logout_payload,
    telethon_reset_state_payload,
    telethon_status_payload,
)
from afkbot.services.channels.endpoint_service import get_channel_endpoint_service


def register_telethon_commands(channel_app: typer.Typer) -> None:
    """Register Telethon commands while preserving the legacy facade import path."""

    register_telethon_command_tree(channel_app)


__all__ = [
    "get_channel_endpoint_service",
    "load_telethon_endpoint",
    "raise_telethon_channel_error",
    "register_telethon_commands",
    "reload_install_managed_runtime_notice",
    "render_telethon_status_payload",
    "set_telethon_endpoint_enabled",
    "telethon_authorize_payload",
    "telethon_dialogs_payload",
    "telethon_logout_payload",
    "telethon_reset_state_payload",
    "telethon_status_payload",
]
