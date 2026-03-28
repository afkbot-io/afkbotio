"""Global bootstrap/system-prompt CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.bootstrap_service import BootstrapService, BootstrapServiceError
from afkbot.settings import get_settings


def _emit_bootstrap_error(exc: Exception) -> None:
    """Render one structured bootstrap error payload."""

    if isinstance(exc, BootstrapServiceError):
        payload = {"ok": False, "error_code": exc.error_code, "reason": exc.reason}
    else:
        payload = {"ok": False, "error_code": "bootstrap_error", "reason": str(exc)}
    typer.echo(json.dumps(payload, ensure_ascii=True))


def register(app: typer.Typer) -> None:
    """Register global bootstrap commands."""

    bootstrap_app = typer.Typer(
        help="Manage global bootstrap/system-prompt markdown files.",
        no_args_is_help=True,
    )
    app.add_typer(bootstrap_app, name="bootstrap")

    @bootstrap_app.command("list")
    def list_bootstrap() -> None:
        """List configured global bootstrap files."""

        settings = get_settings()
        try:
            items = BootstrapService(settings).list()
        except BootstrapServiceError as exc:
            _emit_bootstrap_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {"bootstrap_files": [item.model_dump(mode="json") for item in items]},
                ensure_ascii=True,
            )
        )

    @bootstrap_app.command("show")
    def show_bootstrap(
        file_name: str = typer.Argument(..., help="Bootstrap file name, for example AGENTS.md."),
    ) -> None:
        """Show one global bootstrap file."""

        settings = get_settings()
        try:
            item = BootstrapService(settings).get(file_name=file_name)
        except BootstrapServiceError as exc:
            _emit_bootstrap_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"bootstrap": item.model_dump(mode="json")}, ensure_ascii=True))

    @bootstrap_app.command("set")
    def set_bootstrap(
        file_name: str = typer.Argument(..., help="Bootstrap file name, for example AGENTS.md."),
        text: str | None = typer.Option(
            None,
            "--text",
            help="Inline markdown content for the bootstrap file.",
        ),
        from_file: Path | None = typer.Option(
            None,
            "--from-file",
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
            help="Read markdown content from an existing local file.",
        ),
    ) -> None:
        """Create or replace one global bootstrap file."""

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
            item = BootstrapService(settings).write(file_name=file_name, content=content)
        except (BootstrapServiceError, OSError) as exc:
            _emit_bootstrap_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"bootstrap": item.model_dump(mode="json")}, ensure_ascii=True))
