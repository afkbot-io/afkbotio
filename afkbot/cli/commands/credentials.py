"""Credentials command group with human-readable CLI output and optional JSON mode."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
import json
import sys
from typing import Any

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.services.credentials import CredentialsServiceError
from afkbot.services.credentials.cli_service import (
    create_binding_payload,
    delete_binding_payload,
    list_bindings_payload,
    update_binding_payload,
)


def register(app: typer.Typer) -> None:
    """Register credentials command group in Typer app."""

    credentials_app = typer.Typer(
        help="Manage encrypted credential bindings for builtin integrations.",
        no_args_is_help=True,
    )
    app.add_typer(credentials_app, name="credentials")

    @credentials_app.command("create")
    def create(
        app_name: str = typer.Option(..., "--app", help="Integration app name."),
        credential_slug: str = typer.Option(..., "--slug", help="Credential key."),
        value: str | None = typer.Option(
            None,
            "--value",
            hidden=True,
            help="Deprecated insecure secret argument.",
        ),
        value_stdin: bool = typer.Option(
            False,
            "--value-stdin",
            help="Read credential secret value from stdin.",
        ),
        profile_name: str = typer.Option("default", "--profile-name", help="Credential profile name."),
        profile: str = typer.Option("default", "--profile", help="Runtime profile id."),
        replace_existing: bool = typer.Option(
            False,
            "--replace-existing",
            help="Replace existing binding when present.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Emit machine-readable JSON output.",
        ),
    ) -> None:
        """Create encrypted credential binding."""

        resolved_value = _resolve_cli_secret_value(
            value=value,
            value_stdin=value_stdin,
            prompt_label=f"Secret value for {app_name}/{profile_name}/{credential_slug}",
        )
        _run_credentials_command(
            create_binding_payload(
                profile_id=profile,
                app_name=app_name,
                profile_name=profile_name,
                credential_slug=credential_slug,
                value=resolved_value,
                replace_existing=replace_existing,
            ),
            json_output=json_output,
        )

    @credentials_app.command("update")
    def update(
        app_name: str = typer.Option(..., "--app", help="Integration app name."),
        credential_slug: str = typer.Option(..., "--slug", help="Credential key."),
        value: str | None = typer.Option(
            None,
            "--value",
            hidden=True,
            help="Deprecated insecure secret argument.",
        ),
        value_stdin: bool = typer.Option(
            False,
            "--value-stdin",
            help="Read credential secret value from stdin.",
        ),
        profile_name: str = typer.Option("default", "--profile-name", help="Credential profile name."),
        profile: str = typer.Option("default", "--profile", help="Runtime profile id."),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Emit machine-readable JSON output.",
        ),
    ) -> None:
        """Update encrypted credential binding."""

        resolved_value = _resolve_cli_secret_value(
            value=value,
            value_stdin=value_stdin,
            prompt_label=f"Secret value for {app_name}/{profile_name}/{credential_slug}",
        )
        _run_credentials_command(
            update_binding_payload(
                profile_id=profile,
                app_name=app_name,
                profile_name=profile_name,
                credential_slug=credential_slug,
                value=resolved_value,
            ),
            json_output=json_output,
        )

    @credentials_app.command("delete")
    def delete(
        app_name: str = typer.Option(..., "--app", help="Integration app name."),
        credential_slug: str = typer.Option(..., "--slug", help="Credential key."),
        profile_name: str = typer.Option("default", "--profile-name", help="Credential profile name."),
        profile: str = typer.Option("default", "--profile", help="Runtime profile id."),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Emit machine-readable JSON output.",
        ),
    ) -> None:
        """Delete (deactivate) credential binding."""

        _run_credentials_command(
            delete_binding_payload(
                profile_id=profile,
                app_name=app_name,
                profile_name=profile_name,
                credential_slug=credential_slug,
            ),
            json_output=json_output,
        )

    @credentials_app.command("list")
    def list_bindings(
        app_name: str | None = typer.Option(None, "--app", help="Optional app filter."),
        profile_name: str | None = typer.Option(
            None,
            "--profile-name",
            help="Optional credential profile filter.",
        ),
        include_inactive: bool = typer.Option(False, "--include-inactive", help="Include inactive bindings."),
        profile: str = typer.Option("default", "--profile", help="Runtime profile id."),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Emit machine-readable JSON output.",
        ),
    ) -> None:
        """List credential binding metadata visible to the runtime."""

        _run_credentials_command(
            list_bindings_payload(
                profile_id=profile,
                app_name=app_name,
                profile_name=profile_name,
                include_inactive=include_inactive,
            ),
            json_output=json_output,
        )


def _run_credentials_command(
    coro: Coroutine[Any, Any, dict[str, object]],
    *,
    json_output: bool,
) -> None:
    try:
        payload: dict[str, object] = asyncio.run(coro)
    except CredentialsServiceError as exc:
        _emit_error(exc, json_output=json_output)
        raise typer.Exit(code=1) from None
    except ValueError as exc:
        _emit_error(
            CredentialsServiceError(
                error_code="credentials_invalid_name",
                reason=str(exc),
            ),
            json_output=json_output,
        )
        raise typer.Exit(code=1) from None
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=True))
        return
    _render_success(payload)


def _resolve_cli_secret_value(
    *,
    value: str | None,
    value_stdin: bool,
    prompt_label: str,
) -> str:
    if value is not None:
        raise_usage_error(
            "Passing secrets via --value is disabled. Use --value-stdin or secure prompt input."
        )
    if value_stdin:
        raw = sys.stdin.read()
        if raw.endswith("\r\n"):
            raw = raw[:-2]
        elif raw.endswith("\n"):
            raw = raw[:-1]
        if not raw:
            raise_usage_error("Secret value cannot be empty")
        return raw
    prompted = str(typer.prompt(prompt_label, hide_input=True))
    if not prompted:
        raise_usage_error("Secret value cannot be empty")
    return prompted


def _emit_error(exc: CredentialsServiceError, *, json_output: bool) -> None:
    payload = {
        "ok": False,
        "error_code": exc.error_code,
        "reason": exc.reason,
        "metadata": {str(key): value for key, value in exc.details.items()},
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=True))
        return
    typer.echo(f"ERROR [{exc.error_code}] {exc.reason}")


def _render_success(payload: dict[str, object]) -> None:
    if "binding" in payload:
        binding = payload["binding"]
        if isinstance(binding, dict):
            typer.echo(
                "Credential saved: "
                f"{binding.get('APP_NAME')}/{binding.get('PROFILE_NAME')}/{binding.get('CREDENTIAL_SLUG')} "
                f"for profile `{binding.get('profile_id')}`."
            )
            return
    if payload.get("deleted") is True:
        typer.echo(
            "Credential deleted: "
            f"{payload.get('app_name')}/{payload.get('profile_name')}/{payload.get('credential_slug')}."
        )
        return
    bindings = payload.get("bindings")
    if isinstance(bindings, list):
        raw_count = payload.get("count")
        if isinstance(raw_count, bool):
            count = 0
        elif isinstance(raw_count, int):
            count = raw_count
        elif isinstance(raw_count, str) and raw_count.strip():
            count = int(raw_count)
        else:
            count = 0
        typer.echo(f"Found {count} credential binding(s).")
        for item in bindings:
            if not isinstance(item, dict):
                continue
            typer.echo(
                f"- {item.get('APP_NAME')}/{item.get('PROFILE_NAME')}/{item.get('CREDENTIAL_SLUG')} "
                f"(profile={item.get('profile_id')}, active={item.get('is_active')})"
            )
        return
    typer.echo("OK")
