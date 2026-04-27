"""CLI commands for AFKBOT diagnostic log files."""

from __future__ import annotations

from pathlib import Path

import typer

from afkbot.services.error_logging import (
    component_log_path,
    describe_log_file,
    list_log_files,
    logs_dir,
    remove_log_files,
    tail_log_file,
)
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register diagnostic log commands."""

    logs_app = typer.Typer(
        help="Inspect AFKBOT diagnostic error logs.",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(logs_app, name="logs")

    @logs_app.callback()
    def logs_group(ctx: typer.Context) -> None:
        """Show the log directory and current log files."""

        if ctx.invoked_subcommand is not None:
            return
        _render_summary()

    @logs_app.command("path")
    def show_path() -> None:
        """Print the runtime log directory path."""

        typer.echo(logs_dir(get_settings()))

    @logs_app.command("list")
    def list_logs() -> None:
        """List current and rotated error log files."""

        _render_summary()

    @logs_app.command("tail")
    def tail_logs(
        component: str | None = typer.Option(
            None,
            "--component",
            "-c",
            help="Component folder to read, for example api, runtime, taskflow, or cli.",
        ),
        lines: int = typer.Option(80, "--lines", "-n", min=1, max=1000, help="Lines to print."),
    ) -> None:
        """Print the newest lines from one log file."""

        settings = get_settings()
        path = component_log_path(settings, component) if component else _newest_log_file()
        if path is None or not path.exists():
            typer.echo(f"No log file found. Log directory: {logs_dir(settings)}")
            return
        typer.echo(f"File: {path}")
        contents = tail_log_file(path, lines=lines)
        if contents:
            typer.echo(contents)

    @logs_app.command("clean")
    def clean_logs(
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Confirm deletion of current and rotated AFKBOT error logs.",
        ),
    ) -> None:
        """Delete AFKBOT diagnostic error logs."""

        if not yes:
            typer.echo("Refusing to delete logs without --yes.", err=True)
            raise typer.Exit(code=2)
        removed = remove_log_files(get_settings())
        typer.echo(f"Removed {len(removed)} log file(s).")


def _render_summary() -> None:
    settings = get_settings()
    root = logs_dir(settings)
    typer.echo(f"Log directory: {root}")
    files = list_log_files(settings)
    if not files:
        typer.echo("No log files found.")
        return
    for path in files:
        metadata = describe_log_file(path, root=root)
        modified_at = metadata["modified_at"].strftime("%Y-%m-%d %H:%M:%S UTC")
        typer.echo(
            f"{metadata['relative_path']}  {metadata['size_bytes']} bytes  modified {modified_at}"
        )


def _newest_log_file() -> Path | None:
    files = list_log_files(get_settings())
    return files[0] if files else None
