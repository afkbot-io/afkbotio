"""Shell sandbox backend status and installation commands."""

from __future__ import annotations

import json

import typer

from afkbot.cli.presentation.prompt_i18n import msg, resolve_prompt_language
from afkbot.cli.presentation.setup_prompts import prompt_confirm
from afkbot.services.tools.shell_sandbox import (
    ShellSandboxBackendStatus,
    get_shell_sandbox_backend_status,
    install_shell_sandbox_backend,
)
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register sandbox command group."""

    sandbox_app = typer.Typer(
        help="Inspect and install host shell sandbox backends used by bash.exec.",
        no_args_is_help=True,
    )
    app.add_typer(sandbox_app, name="sandbox")

    @sandbox_app.command("status")
    def status(
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Check whether OS-level shell sandboxing is ready."""

        result = get_shell_sandbox_backend_status()
        if json_output:
            typer.echo(json.dumps(_status_payload(result), ensure_ascii=True))
        else:
            typer.echo(_format_status(result))
        if not result.ok:
            raise typer.Exit(code=1)

    @sandbox_app.command("install")
    def install(
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Run the detected package-manager command without confirmation.",
        ),
        json_output: bool = typer.Option(
            False,
            "--json",
            help="Print deterministic JSON payload.",
        ),
    ) -> None:
        """Install the host shell sandbox backend when a supported command is known."""

        initial = get_shell_sandbox_backend_status()
        if initial.ok:
            if json_output:
                typer.echo(json.dumps(_status_payload(initial), ensure_ascii=True))
            else:
                typer.echo(_format_status(initial))
            return
        if not initial.install_command:
            if json_output:
                typer.echo(json.dumps(_status_payload(initial), ensure_ascii=True))
            else:
                typer.echo(_format_status(initial))
            raise typer.Exit(code=1)
        if not yes and not json_output:
            settings = get_settings()
            lang = resolve_prompt_language(settings=settings, value=None, ru=False)
            proceed = prompt_confirm(
                question=msg(
                    lang,
                    en=(
                        "AFKBOT will run this command to install the shell sandbox backend:\n"
                        f"`{' '.join(initial.install_command)}`\n\nContinue?"
                    ),
                    ru=(
                        "AFKBOT выполнит эту команду для установки shell sandbox backend:\n"
                        f"`{' '.join(initial.install_command)}`\n\nПродолжить?"
                    ),
                ),
                title=msg(lang, en="Sandbox: Install backend", ru="Sandbox: Установка backend"),
                default=False,
                lang=lang,
            )
            if not proceed:
                typer.echo(msg(lang, en="Sandbox install cancelled.", ru="Установка отменена."))
                raise typer.Exit(code=0)
        result = install_shell_sandbox_backend()
        if json_output:
            typer.echo(json.dumps(_status_payload(result), ensure_ascii=True))
        else:
            typer.echo(_format_status(result))
        if not result.ok:
            raise typer.Exit(code=1)


def _status_payload(result: ShellSandboxBackendStatus) -> dict[str, object]:
    return {
        "ok": result.ok,
        "sandbox_kind": result.sandbox_kind,
        "helper_path": result.helper_path,
        "reason": result.reason,
        "install_command": list(result.install_command),
    }


def _format_status(result: ShellSandboxBackendStatus) -> str:
    state = "ok" if result.ok else "missing"
    text = f"sandbox: {state} ({result.sandbox_kind}) - {result.reason}"
    if result.helper_path:
        text = f"{text}\nhelper_path: {result.helper_path}"
    if result.install_command:
        text = f"{text}\ninstall_command: {' '.join(result.install_command)}"
    return text
