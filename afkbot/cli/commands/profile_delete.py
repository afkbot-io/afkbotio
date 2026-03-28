"""Profile deletion CLI command."""

from __future__ import annotations

import asyncio

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.profile_runtime import ProfileServiceError, get_profile_service
from afkbot.settings import get_settings


def register_delete(profile_app: typer.Typer) -> None:
    """Register `afk profile delete`."""

    @profile_app.command("delete")
    def delete(
        profile_id_arg: str | None = typer.Argument(None, help="Profile id to delete."),
        profile_id: str | None = typer.Option(None, "--id", help="Profile id to delete."),
        yes: bool = typer.Option(False, "--yes", help="Delete without confirmation prompt."),
    ) -> None:
        """Delete one profile-agent, its runtime folder, and linked runtime data."""

        settings = get_settings()
        raw_profile_id = (profile_id or profile_id_arg or "").strip()
        if not raw_profile_id:
            raise_usage_error("Profile id is required. Use `afk profile delete <id>`.")
        try:
            normalized_profile_id = validate_profile_id(raw_profile_id)
        except ValueError as exc:
            raise_usage_error(str(exc))
        if not yes:
            confirmed = typer.confirm(
                f"Delete profile `{normalized_profile_id}` with its runtime data and profile folder?",
                default=False,
            )
            if not confirmed:
                raise_usage_error("Profile deletion cancelled.", code=1)
        try:
            deleted = asyncio.run(get_profile_service(settings).delete(profile_id=normalized_profile_id))
        except ProfileServiceError as exc:
            raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
        typer.echo(f"Profile `{deleted.id}` deleted.")
