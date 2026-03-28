"""CLI commands for automation CRUD, delivery defaults, and operator triggers."""

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
from afkbot.services.channels.contracts import ChannelDeliveryTarget


def register(app: typer.Typer) -> None:
    """Register automation CLI group."""

    automation_app = typer.Typer(
        help="Manage profile automations and persisted delivery defaults.",
        no_args_is_help=True,
    )
    app.add_typer(automation_app, name="automation")

    @automation_app.command("list")
    def list_automations(
        profile: str = typer.Option("default", "--profile", help="Target profile id."),
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
                    profile_id=profile,
                    include_deleted=include_deleted,
                )
            )
        )

    @automation_app.command("show")
    def show_automation(
        automation_id: int = typer.Argument(..., min=1, help="Automation id."),
        profile: str = typer.Option("default", "--profile", help="Target profile id."),
    ) -> None:
        """Show one automation metadata record."""

        typer.echo(
            asyncio.run(
                get_automation_payload(
                    profile_id=profile,
                    automation_id=automation_id,
                )
            )
        )

    @automation_app.command("create")
    def create_automation(
        profile: str = typer.Option("default", "--profile", help="Target profile id."),
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
        delivery_mode: str | None = typer.Option(
            None,
            "--delivery-mode",
            help="Delivery mode: target, tool, or none.",
        ),
        timezone_name: str = typer.Option(
            "UTC",
            "--timezone",
            help="IANA timezone for cron trigger.",
        ),
        delivery_transport: str | None = typer.Option(None, "--delivery-transport"),
        delivery_binding_id: str | None = typer.Option(None, "--delivery-binding-id"),
        delivery_account_id: str | None = typer.Option(None, "--delivery-account-id"),
        delivery_peer_id: str | None = typer.Option(None, "--delivery-peer-id"),
        delivery_thread_id: str | None = typer.Option(None, "--delivery-thread-id"),
        delivery_user_id: str | None = typer.Option(None, "--delivery-user-id"),
        delivery_address: str | None = typer.Option(None, "--delivery-address"),
        delivery_subject: str | None = typer.Option(None, "--delivery-subject"),
    ) -> None:
        """Create one automation under the selected profile."""

        payload = asyncio.run(
            create_automation_payload(
                profile_id=profile,
                name=name,
                prompt=prompt,
                trigger_type=trigger,
                cron_expr=cron_expr,
                timezone_name=timezone_name,
                delivery_mode=delivery_mode,
                delivery_target=_build_delivery_target(
                    transport=delivery_transport,
                    binding_id=delivery_binding_id,
                    account_id=delivery_account_id,
                    peer_id=delivery_peer_id,
                    thread_id=delivery_thread_id,
                    user_id=delivery_user_id,
                    address=delivery_address,
                    subject=delivery_subject,
                ),
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @automation_app.command("update")
    def update_automation(
        automation_id: int = typer.Argument(..., min=1, help="Automation id."),
        profile: str = typer.Option("default", "--profile", help="Target profile id."),
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
        delivery_mode: str | None = typer.Option(
            None,
            "--delivery-mode",
            help="Delivery mode: target, tool, or none.",
        ),
        clear_delivery_target: bool = typer.Option(
            False,
            "--clear-delivery-target",
            help="Remove persisted outbound delivery target.",
        ),
        delivery_transport: str | None = typer.Option(None, "--delivery-transport"),
        delivery_binding_id: str | None = typer.Option(None, "--delivery-binding-id"),
        delivery_account_id: str | None = typer.Option(None, "--delivery-account-id"),
        delivery_peer_id: str | None = typer.Option(None, "--delivery-peer-id"),
        delivery_thread_id: str | None = typer.Option(None, "--delivery-thread-id"),
        delivery_user_id: str | None = typer.Option(None, "--delivery-user-id"),
        delivery_address: str | None = typer.Option(None, "--delivery-address"),
        delivery_subject: str | None = typer.Option(None, "--delivery-subject"),
    ) -> None:
        """Update one automation and optionally rotate or clear delivery defaults."""

        payload = asyncio.run(
            update_automation_payload(
                profile_id=profile,
                automation_id=automation_id,
                name=name,
                prompt=prompt,
                status=status,
                cron_expr=cron_expr,
                timezone_name=timezone_name,
                rotate_webhook_token=rotate_webhook_token,
                delivery_mode=delivery_mode,
                delivery_target=_build_delivery_target(
                    transport=delivery_transport,
                    binding_id=delivery_binding_id,
                    account_id=delivery_account_id,
                    peer_id=delivery_peer_id,
                    thread_id=delivery_thread_id,
                    user_id=delivery_user_id,
                    address=delivery_address,
                    subject=delivery_subject,
                ),
                clear_delivery_target=clear_delivery_target,
            )
        )
        typer.echo(payload)
        _exit_on_error_payload(payload)

    @automation_app.command("delete")
    def delete_automation(
        automation_id: int = typer.Argument(..., min=1, help="Automation id."),
        profile: str = typer.Option("default", "--profile", help="Target profile id."),
    ) -> None:
        """Soft-delete one automation."""

        payload = asyncio.run(
            delete_automation_payload(
                profile_id=profile,
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
        token: str = typer.Option(..., "--token", help="Plaintext webhook token."),
        payload_json: str = typer.Option(
            "{}",
            "--payload-json",
            help="Optional JSON object payload to send into the webhook automation.",
        ),
    ) -> None:
        """Trigger one webhook automation immediately from CLI."""

        payload = asyncio.run(trigger_webhook_payload(token=token, payload_json=payload_json))
        typer.echo(payload)
        _exit_on_error_payload(payload)


def _build_delivery_target(
    *,
    transport: str | None,
    binding_id: str | None,
    account_id: str | None,
    peer_id: str | None,
    thread_id: str | None,
    user_id: str | None,
    address: str | None,
    subject: str | None,
) -> ChannelDeliveryTarget | None:
    fields = {
        "transport": transport,
        "binding_id": binding_id,
        "account_id": account_id,
        "peer_id": peer_id,
        "thread_id": thread_id,
        "user_id": user_id,
        "address": address,
        "subject": subject,
    }
    if not any(value is not None and str(value).strip() for value in fields.values()):
        return None
    try:
        return ChannelDeliveryTarget.model_validate(fields)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None


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
