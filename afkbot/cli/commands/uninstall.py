"""Uninstall command for clearing local AFKBOT state."""

from __future__ import annotations

import sys

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.presentation.uninstall_prompts import prompt_uninstall_confirmation
from afkbot.services.managed_runtime_service import remove_managed_runtime_service
from afkbot.services.setup.project_cleanup import clear_local_sqlite_db, clear_profiles_workspace
from afkbot.services.setup.runtime_store import clear_runtime_store
from afkbot.services.setup.state import clear_setup_state
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register uninstall command in Typer app."""

    @app.command("uninstall")
    def uninstall(
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Skip confirmation and fully remove local AFKBOT state.",
        ),
    ) -> None:
        """Remove local AFKBOT state from the current runtime root."""

        settings = get_settings()
        _confirm_uninstall(yes=yes)
        try:
            service_result = remove_managed_runtime_service()
            _handle_uninstall_service_result(service_result)
            clear_profiles_workspace(settings)
            clear_local_sqlite_db(settings)
            clear_setup_state(settings)
            clear_runtime_store(settings)
            get_settings.cache_clear()
            typer.echo(_format_uninstall_success())
        except ValueError as exc:
            raise_usage_error(str(exc))


def _confirm_uninstall(*, yes: bool) -> None:
    """Require one explicit confirmation before full uninstall."""

    if yes:
        return
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise_usage_error("confirmation required; rerun with --yes")
    if prompt_uninstall_confirmation():
        return
    raise_usage_error("uninstall cancelled by user")


def _format_uninstall_success(
) -> str:
    lines = ["AFKBOT uninstall complete."]
    lines.append("Local SQLite database: removed when present")
    lines.append("Runtime config: removed")
    lines.append("Profiles workspace: removed")
    lines.append("Install state: cleared")
    lines.append("Next step: run `afk setup` to configure the default profile again.")
    return "\n".join(lines)


def _handle_uninstall_service_result(result: object) -> None:
    status = str(getattr(result, "status", "unknown"))
    reason = str(getattr(result, "reason", "") or "").strip()
    if status == "failed":
        raise_usage_error(reason or "failed to remove the managed AFKBOT service")
    if status == "manual_restart_required" and reason:
        typer.echo(f"WARNING {reason}", err=True)
