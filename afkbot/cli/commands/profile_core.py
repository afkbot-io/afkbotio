"""Core profile CLI command registration."""

from __future__ import annotations

import typer

from afkbot.cli.commands.profile_add import register_add
from afkbot.cli.commands.profile_delete import register_delete
from afkbot.cli.commands.profile_read import register_read
from afkbot.cli.commands.profile_secrets import register_secrets
from afkbot.cli.commands.profile_update import register_update


def register_core(profile_app: typer.Typer) -> None:
    """Register core CRUD commands under `afk profile`."""

    register_add(profile_app)
    register_update(profile_app)
    register_delete(profile_app)
    register_read(profile_app)
    register_secrets(profile_app)
