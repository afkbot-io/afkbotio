"""Root CLI version command."""

from __future__ import annotations

import typer

from afkbot.version import load_cli_version_info


def register(app: typer.Typer) -> None:
    """Register `afk version` command."""

    @app.command("version")
    def version() -> None:
        """Print installed AFKBOT version and git checkout metadata when available."""

        typer.echo(load_cli_version_info().render())
