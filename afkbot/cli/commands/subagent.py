"""Top-level CLI commands for profile-local subagent descriptors."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from afkbot.cli.commands.runtime_assets_common import emit_structured_error, resolve_inline_or_file_text
from afkbot.services.policy import PolicyViolationError
from afkbot.services.profile_id import InvalidProfileIdError, validate_profile_id
from afkbot.services.profile_runtime.service import ProfileServiceError, get_profile_service
from afkbot.services.subagents import get_subagent_service
from afkbot.services.subagents.profile_service import get_profile_subagent_service
from afkbot.services.tools.base import ToolContext
from afkbot.settings import get_settings


def register(app: typer.Typer) -> None:
    """Register `afk subagent ...` commands."""

    subagent_app = typer.Typer(
        help="Manage profile-local subagent descriptors.",
        no_args_is_help=True,
    )
    app.add_typer(subagent_app, name="subagent")

    @subagent_app.command("list")
    def list_subagents(
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
    ) -> None:
        """List profile-local subagents for one profile."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            items = asyncio.run(
                get_profile_subagent_service(settings).list(
                    profile_id=normalized_profile_id,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, FileNotFoundError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="subagent_error")
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {"subagents": [item.model_dump(mode="json") for item in items]},
                ensure_ascii=True,
            )
        )

    @subagent_app.command("show")
    def show_subagent(
        name: str = typer.Argument(..., help="Profile-local subagent name."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
    ) -> None:
        """Show one profile-local subagent descriptor."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            item = asyncio.run(
                get_profile_subagent_service(settings).get(
                    profile_id=normalized_profile_id,
                    name=name,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, FileNotFoundError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="subagent_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"subagent": item.model_dump(mode="json")}, ensure_ascii=True))

    @subagent_app.command("set")
    def set_subagent(
        name: str = typer.Argument(..., help="Profile-local subagent name."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        text: str | None = typer.Option(None, "--text", help="Inline subagent markdown."),
        from_file: Path | None = typer.Option(
            None,
            "--from-file",
            exists=True,
            dir_okay=False,
            file_okay=True,
            readable=True,
            resolve_path=True,
            help="Read subagent markdown from a local file.",
        ),
    ) -> None:
        """Create or replace one profile-local subagent descriptor."""

        try:
            markdown = resolve_inline_or_file_text(text=text, from_file=from_file)
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            item = asyncio.run(
                get_profile_subagent_service(settings).upsert(
                    profile_id=normalized_profile_id,
                    name=name,
                    content=markdown,
                )
            )
        except (InvalidProfileIdError, OSError, ProfileServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="subagent_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"subagent": item.model_dump(mode="json")}, ensure_ascii=True))

    @subagent_app.command("delete")
    def delete_subagent(
        name: str = typer.Argument(..., help="Profile-local subagent name."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
    ) -> None:
        """Delete one profile-local subagent descriptor."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            item = asyncio.run(
                get_profile_subagent_service(settings).delete(
                    profile_id=normalized_profile_id,
                    name=name,
                )
            )
        except (InvalidProfileIdError, ProfileServiceError, FileNotFoundError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="subagent_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"subagent": item.model_dump(mode="json")}, ensure_ascii=True))

    @subagent_app.command("run")
    def run_subagent(
        prompt: str = typer.Option(..., "--prompt", help="Prompt sent to the child subagent."),
        session_id: str = typer.Option(..., "--session", help="Owning parent session id."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        name: str | None = typer.Option(None, "--name", help="Optional subagent descriptor name."),
        timeout_sec: int | None = typer.Option(
            None,
            "--timeout-sec",
            min=1,
            help="Optional run timeout for the spawned subagent task.",
        ),
    ) -> None:
        """Start one persisted subagent task."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            result = asyncio.run(
                get_subagent_service(settings).run(
                    ctx=ToolContext(profile_id=normalized_profile_id, session_id=session_id, run_id=0),
                    prompt=prompt,
                    subagent_name=name,
                    timeout_sec=timeout_sec,
                )
            )
        except (
            InvalidProfileIdError,
            ProfileServiceError,
            FileNotFoundError,
            PermissionError,
            PolicyViolationError,
            ValueError,
        ) as exc:
            emit_structured_error(exc, default_error_code="subagent_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"task": result.model_dump(mode="json")}, ensure_ascii=True))

    @subagent_app.command("wait")
    def wait_subagent(
        task_id: str = typer.Argument(..., help="Persisted subagent task id."),
        session_id: str = typer.Option(..., "--session", help="Owning parent session id."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
        timeout_sec: int | None = typer.Option(
            None,
            "--timeout-sec",
            min=1,
            help="Optional wait timeout in seconds.",
        ),
    ) -> None:
        """Wait for one persisted subagent task."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            result = asyncio.run(
                get_subagent_service(settings).wait(
                    task_id=task_id,
                    timeout_sec=timeout_sec,
                    profile_id=normalized_profile_id,
                    session_id=session_id,
                )
            )
        except (KeyError, PermissionError):
            emit_structured_error(
                ValueError(f"Subagent task not found: {task_id}"),
                default_error_code="subagent_task_not_found",
            )
            raise typer.Exit(code=1) from None
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="subagent_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"task": result.model_dump(mode="json")}, ensure_ascii=True))

    @subagent_app.command("result")
    def subagent_result(
        task_id: str = typer.Argument(..., help="Persisted subagent task id."),
        session_id: str = typer.Option(..., "--session", help="Owning parent session id."),
        profile_id: str = typer.Option("default", "--profile", help="Runtime profile id."),
    ) -> None:
        """Show current or final result for one persisted subagent task."""

        try:
            settings = get_settings()
            normalized_profile_id = validate_profile_id(profile_id)
            asyncio.run(get_profile_service(settings).get(profile_id=normalized_profile_id))
            result = asyncio.run(
                get_subagent_service(settings).result(
                    task_id=task_id,
                    profile_id=normalized_profile_id,
                    session_id=session_id,
                )
            )
        except (KeyError, PermissionError):
            emit_structured_error(
                ValueError(f"Subagent task not found: {task_id}"),
                default_error_code="subagent_task_not_found",
            )
            raise typer.Exit(code=1) from None
        except (InvalidProfileIdError, ProfileServiceError, ValueError) as exc:
            emit_structured_error(exc, default_error_code="subagent_error")
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"task": result.model_dump(mode="json")}, ensure_ascii=True))
