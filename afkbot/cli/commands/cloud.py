"""Cloud connection CLI commands."""

from __future__ import annotations

from dataclasses import asdict
import json

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.cloud_remote import (
    CloudRemoteError,
    list_remote_bot_connections,
    resolve_cloud_api_url,
    save_remote_bot_connection,
    verify_remote_bot_connection,
)
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register Cloud commands."""

    cloud_app = typer.Typer(
        help="Connect local AFKBOT to AFKBOT Cloud bots.",
        no_args_is_help=True,
    )

    @cloud_app.command("connect")
    def connect(
        url: str = typer.Option(..., "--url", help="Public bot URL from AFKBOT Cloud."),
        token: str | None = typer.Option(
            None,
            "--token",
            help="Remote connection token. Omit to enter it as a hidden prompt.",
        ),
        api_url: str | None = typer.Option(
            None,
            "--api-url",
            help="Cloud API base URL. Defaults to AFKBOT_CLOUD_API_URL, inferred Cloud URL, or production Cloud API.",
        ),
        name: str = typer.Option(
            "default",
            "--name",
            help="Local name for this remote bot connection.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """Verify and save a remote AFKBOT Cloud connection."""

        try:
            resolved_api_url = resolve_cloud_api_url(api_url, public_url=url)
            resolved_token = token or typer.prompt("Cloud token", hide_input=True)
            payload = verify_remote_bot_connection(
                api_url=resolved_api_url,
                public_url=url,
                token=resolved_token,
            )
            connection = save_remote_bot_connection(
                settings=get_settings(),
                name=name,
                api_url=resolved_api_url,
                public_url=url,
                token=resolved_token,
                verification_payload=payload,
            )
        except CloudRemoteError as exc:
            _render_error(exc, json_output=json_output)
            return

        if json_output:
            typer.echo(json.dumps({"ok": True, "connection": asdict(connection)}, ensure_ascii=True))
            return
        typer.echo(f"connected: {connection.name}")
        typer.echo(f"bot: {connection.bot_name or connection.bot_id}")
        typer.echo(f"url: {connection.public_url}")

    @cloud_app.command("list")
    def list_connections(
        json_output: bool = typer.Option(False, "--json", help="Print deterministic JSON payload."),
    ) -> None:
        """List saved remote AFKBOT Cloud connections."""

        connections = list_remote_bot_connections(settings=get_settings())
        if json_output:
            typer.echo(json.dumps({"ok": True, "connections": [asdict(item) for item in connections]}, ensure_ascii=True))
            return
        if not connections:
            typer.echo("No Cloud connections saved.")
            return
        for connection in connections:
            typer.echo(f"{connection.name}: {connection.bot_name or connection.bot_id} {connection.public_url}")

    app.add_typer(cloud_app, name="cloud")


def _render_error(exc: CloudRemoteError, *, json_output: bool) -> None:
    """Render a Cloud remote error consistently.

    :param exc: Expected Cloud remote error.
    :param json_output: Whether the command is in machine-readable mode.
    :return: None.
    """

    if json_output:
        typer.echo(json.dumps({"ok": False, "error_code": exc.error_code, "reason": exc.reason}, ensure_ascii=True))
        raise typer.Exit(code=1)
    raise_usage_error(exc.reason)
