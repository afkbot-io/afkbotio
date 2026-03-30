"""AFKBOT install update command."""

from __future__ import annotations

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.update_runtime import (
    UpdateRuntimeError,
    format_update_success,
    run_update,
)
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register `afk update` command."""

    @app.command("update")
    def update() -> None:
        """Refresh the active AFKBOT install and apply runtime maintenance."""

        settings = get_settings()
        try:
            result = run_update(settings)
        except UpdateRuntimeError as exc:
            raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}", code=1)
        typer.echo(format_update_success(result))
