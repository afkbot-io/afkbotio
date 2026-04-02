"""CLI commands for automation CRUD and operator triggers."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import typer

from afkbot.services.automations.cli_service import (
    create_automation_payload,
    delete_automation_payload,
    get_automation_payload,
    list_automations_payload,
    update_automation_payload,
)
from afkbot.services.automations.runtime_service import tick_cron_payload, trigger_webhook_payload


def register(app: typer.Typer) -> None:
    """Register automation CLI group."""

    automation_app = typer.Typer(
        help="Manage profile automations and runtime triggers.",
        no_args_is_help=True,
    )
    app.add_typer(automation_app, name="automation")

    @automation_app.callback()
    def automation_group(
        ctx: typer.Context,
        profile: str | None = typer.Option(
            None,
            "--profile",
            help="Default target profile id for automation subcommands.",
        ),
    ) -> None:
        """Capture optional group-level automation defaults."""

        ctx.ensure_object(dict)
        if profile is not None:
            ctx.obj["profile"] = profile

    @automation_app.command("list")
    def list_automations(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        include_deleted: bool = typer.Option(
            False,
            "--include-deleted/--no-include-deleted",
            help="Include soft-deleted automation rows.",
        ),
    ) -> None:
        """List automations for one profile."""

        typer.echo(
            asyncio.run(
                list_automations_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    include_deleted=include_deleted,
                )
            )
        )

    @automation_app.command("show")
    @automation_app.command("get")
    def show_automation(
        ctx: typer.Context,
        automation_id: int = typer.Argument(..., min=1, help="Automation id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
    ) -> None:
        """Show one automation metadata record."""

        typer.echo(
            asyncio.run(
                get_automation_payload(
                    profile_id=_resolve_profile(ctx, profile),
                    automation_id=automation_id,
                )
            )
        )

    @automation_app.command("create")
    def create_automation(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        name: str = typer.Option(..., "--name", help="Automation name."),
        prompt: str = typer.Option(..., "--prompt", help="Automation task prompt."),
        trigger: str = typer.Option(
            ...,
            "--trigger",
            help="Automation trigger type: cron or webhook.",
        ),
        cron_expr: str | None = typer.Option(
            None,
            "--cron-expr",
            help="Cron expression required for cron trigger.",
        ),
        timezone_name: str = typer.Option(
            "UTC",
            "--timezone",
            help="IANA timezone for cron trigger.",
        ),
    ) -> None:
        """Create one automation under the selected profile."""

        payload = asyncio.run(
            create_automation_payload(
                profile_id=_resolve_profile(ctx, profile),
                name=name,
                prompt=prompt,
                trigger_type=trigger,
                cron_expr=cron_expr,
                timezone_name=timezone_name,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @automation_app.command("update")
    def update_automation(
        ctx: typer.Context,
        automation_id: int = typer.Argument(..., min=1, help="Automation id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        name: str | None = typer.Option(None, "--name", help="Updated automation name."),
        prompt: str | None = typer.Option(None, "--prompt", help="Updated task prompt."),
        status: str | None = typer.Option(
            None,
            "--status",
            help="Updated status: active or paused.",
        ),
        cron_expr: str | None = typer.Option(None, "--cron-expr", help="Updated cron expression."),
        timezone_name: str | None = typer.Option(
            None,
            "--timezone",
            help="Updated cron timezone.",
        ),
        rotate_webhook_token: bool = typer.Option(
            False,
            "--rotate-webhook-token",
            help="Rotate webhook trigger token for webhook automations.",
        ),
    ) -> None:
        """Update one automation fields and optionally rotate webhook token."""

        payload = asyncio.run(
            update_automation_payload(
                profile_id=_resolve_profile(ctx, profile),
                automation_id=automation_id,
                name=name,
                prompt=prompt,
                status=status,
                cron_expr=cron_expr,
                timezone_name=timezone_name,
                rotate_webhook_token=rotate_webhook_token,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @automation_app.command("delete")
    def delete_automation(
        ctx: typer.Context,
        automation_id: int = typer.Argument(..., min=1, help="Automation id."),
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
    ) -> None:
        """Soft-delete one automation."""

        payload = asyncio.run(
            delete_automation_payload(
                profile_id=_resolve_profile(ctx, profile),
                automation_id=automation_id,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @automation_app.command("cron-tick")
    def cron_tick(
        now_utc: str | None = typer.Option(
            None,
            "--now-utc",
            help="Optional ISO-8601 UTC timestamp override for due-job evaluation.",
        ),
    ) -> None:
        """Run one cron scheduler tick immediately and print triggered/failed ids."""

        effective_now = _parse_now_utc(now_utc)
        payload = asyncio.run(tick_cron_payload(now_utc=effective_now))
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @automation_app.command("webhook-trigger")
    def webhook_trigger(
        ctx: typer.Context,
        profile: str | None = typer.Option(None, "--profile", help="Target profile id."),
        token: str = typer.Option(..., "--token", help="Plaintext webhook token."),
        payload_json: str = typer.Option(
            "{}",
            "--payload-json",
            help="Optional JSON object payload to send into the webhook automation.",
        ),
    ) -> None:
        """Trigger one webhook automation immediately from CLI."""

        payload = asyncio.run(
            trigger_webhook_payload(
                profile_id=_resolve_profile(ctx, profile),
                token=token,
                payload_json=payload_json,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)


def _resolve_profile(ctx: typer.Context, profile: str | None) -> str:
    """Resolve subcommand profile from explicit flag, group option, or default."""

    if profile is not None and profile.strip():
        return profile
    if isinstance(ctx.obj, dict):
        group_profile = ctx.obj.get("profile")
        if isinstance(group_profile, str) and group_profile.strip():
            return group_profile
    return "default"


def _exit_on_error_payload(payload: str) -> None:
    data = json.loads(payload)
    if data.get("ok") is False:
        raise typer.Exit(code=1)


def _parse_now_utc(value: str | None) -> datetime | None:
    """Parse optional ISO timestamp for manual cron ticks."""

    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("--now-utc must be one ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
