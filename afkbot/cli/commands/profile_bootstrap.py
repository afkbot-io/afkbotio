"""Profile bootstrap/system-prompt CLI commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.profile_common import emit_profile_error
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.services.profile_runtime import (
    ProfileServiceError,
    get_profile_bootstrap_service,
)
from afkbot.settings import get_settings


def register_bootstrap(profile_app: typer.Typer) -> None:
    """Register bootstrap override commands under `afk profile bootstrap`."""

    bootstrap_app = typer.Typer(
        help="Manage profile-local bootstrap/system-prompt markdown overrides.",
        no_args_is_help=True,
    )
    profile_app.add_typer(bootstrap_app, name="bootstrap")

    @bootstrap_app.command("list")
    def list_bootstrap(
        profile_id: str = typer.Argument(..., help="Runtime profile id."),
    ) -> None:
        """List available bootstrap override files for one profile."""

        settings = get_settings()
        try:
            items = asyncio.run(
                get_profile_bootstrap_service(settings).list(profile_id=validate_profile_id(profile_id))
            )
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {"bootstrap_files": [item.model_dump(mode="json") for item in items]},
                ensure_ascii=True,
            )
        )

    @bootstrap_app.command("show")
    def show_bootstrap(
        profile_id: str = typer.Argument(..., help="Runtime profile id."),
        file_name: str = typer.Argument(..., help="Bootstrap file name, for example AGENTS.md."),
    ) -> None:
        """Show one bootstrap override file for one profile."""

        settings = get_settings()
        try:
            item = asyncio.run(
                get_profile_bootstrap_service(settings).get(
                    profile_id=validate_profile_id(profile_id),
                    file_name=file_name,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"bootstrap": item.model_dump(mode="json")}, ensure_ascii=True))

    @bootstrap_app.command("set")
    def set_bootstrap(
        profile_id: str = typer.Argument(..., help="Runtime profile id."),
        file_name: str = typer.Argument(..., help="Bootstrap file name, for example AGENTS.md."),
        text: str | None = typer.Option(
            None,
            "--text",
            help="Inline markdown content for the bootstrap override file.",
        ),
        from_file: Path | None = typer.Option(
            None,
            "--from-file",
            exists=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
            help="Read markdown content from an existing local file.",
        ),
    ) -> None:
        """Create or replace one bootstrap override file for one profile."""

        if (text is None) == (from_file is None):
            raise_usage_error("Provide exactly one of --text or --from-file.")
        settings = get_settings()
        try:
            if text is not None:
                content = text
            else:
                if from_file is None:
                    raise AssertionError("from_file must be provided when text is absent")
                content = from_file.read_text(encoding="utf-8")
            item = asyncio.run(
                get_profile_bootstrap_service(settings).write(
                    profile_id=validate_profile_id(profile_id),
                    file_name=file_name,
                    content=content,
                )
            )
        except (InvalidProfileIdError, OSError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"bootstrap": item.model_dump(mode="json")}, ensure_ascii=True))

    @bootstrap_app.command("clear")
    def clear_bootstrap(
        profile_id: str = typer.Argument(..., help="Runtime profile id."),
        file_name: str = typer.Argument(..., help="Bootstrap file name, for example AGENTS.md."),
    ) -> None:
        """Remove one bootstrap override file for one profile."""

        settings = get_settings()
        try:
            item = asyncio.run(
                get_profile_bootstrap_service(settings).remove(
                    profile_id=validate_profile_id(profile_id),
                    file_name=file_name,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"bootstrap": item.model_dump(mode="json")}, ensure_ascii=True))
