"""Profile/channel binding CLI commands."""

from __future__ import annotations

import asyncio
import json

import typer

from afkbot.cli.commands.profile_common import emit_profile_error
from afkbot.services.channel_routing import (
    ChannelBindingRule,
    ChannelBindingServiceError,
    ChannelRoutingInput,
    SessionPolicy,
    get_channel_binding_service,
)
from afkbot.settings import get_settings


def register_binding(profile_app: typer.Typer) -> None:
    """Register binding management commands under `afk profile binding`."""

    binding_app = typer.Typer(
        help="Manage transport-to-profile routing bindings and session policies.",
        no_args_is_help=True,
    )
    profile_app.add_typer(binding_app, name="binding")

    @binding_app.command("set")
    def set_binding(
        binding_id: str = typer.Argument(..., help="Stable binding id."),
        transport: str = typer.Option(..., "--transport", help="Transport name, for example telegram or api."),
        profile_id: str = typer.Option(..., "--profile-id", help="Target runtime profile id."),
        session_policy: SessionPolicy = typer.Option(
            "main",
            "--session-policy",
            help="Session policy: main, per-chat, per-thread, per-user-in-group.",
        ),
        priority: int = typer.Option(0, "--priority", help="Higher priority wins between equally specific rules."),
        enabled: bool = typer.Option(True, "--enabled/--disabled", help="Enable or disable this binding."),
        account_id: str | None = typer.Option(None, "--account-id", help="Optional transport account/bot id."),
        peer_id: str | None = typer.Option(None, "--peer-id", help="Optional chat/group/peer id."),
        thread_id: str | None = typer.Option(None, "--thread-id", help="Optional thread/topic id."),
        user_id: str | None = typer.Option(None, "--user-id", help="Optional user id inside one peer."),
        prompt_overlay: str | None = typer.Option(
            None,
            "--prompt-overlay",
            help="Optional binding-local prompt overlay text.",
        ),
    ) -> None:
        """Create or replace one persisted channel binding rule."""

        settings = get_settings()
        try:
            rule = asyncio.run(
                get_channel_binding_service(settings).put(
                    ChannelBindingRule(
                        binding_id=binding_id,
                        transport=transport,
                        profile_id=profile_id,
                        session_policy=session_policy,
                        priority=priority,
                        enabled=enabled,
                        account_id=account_id,
                        peer_id=peer_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        prompt_overlay=prompt_overlay,
                    )
                )
            )
        except (ChannelBindingServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"binding": rule.model_dump(mode="json")}, ensure_ascii=True))

    @binding_app.command("list")
    def list_bindings(
        transport: str | None = typer.Option(None, "--transport", help="Optional transport filter."),
        profile_id: str | None = typer.Option(None, "--profile-id", help="Optional profile filter."),
    ) -> None:
        """List persisted channel binding rules."""

        settings = get_settings()
        try:
            rules = asyncio.run(
                get_channel_binding_service(settings).list(
                    transport=transport,
                    profile_id=profile_id,
                )
            )
        except (ChannelBindingServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(
            json.dumps(
                {"bindings": [rule.model_dump(mode="json") for rule in rules]},
                ensure_ascii=True,
            )
        )

    @binding_app.command("show")
    def show_binding(
        binding_id: str = typer.Argument(..., help="Stable binding id."),
    ) -> None:
        """Show one persisted channel binding rule."""

        settings = get_settings()
        try:
            rule = asyncio.run(get_channel_binding_service(settings).get(binding_id=binding_id))
        except (ChannelBindingServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"binding": rule.model_dump(mode="json")}, ensure_ascii=True))

    @binding_app.command("delete")
    def delete_binding(
        binding_id: str = typer.Argument(..., help="Stable binding id."),
    ) -> None:
        """Delete one persisted channel binding rule."""

        settings = get_settings()
        try:
            asyncio.run(get_channel_binding_service(settings).delete(binding_id=binding_id))
        except (ChannelBindingServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        typer.echo(json.dumps({"ok": True, "binding_id": binding_id}, ensure_ascii=True))

    @binding_app.command("resolve")
    def resolve_binding(
        transport: str = typer.Option(..., "--transport", help="Transport name."),
        account_id: str | None = typer.Option(None, "--account-id", help="Optional transport account/bot id."),
        peer_id: str | None = typer.Option(None, "--peer-id", help="Optional chat/group/peer id."),
        thread_id: str | None = typer.Option(None, "--thread-id", help="Optional thread/topic id."),
        user_id: str | None = typer.Option(None, "--user-id", help="Optional user id inside one peer."),
        default_session_id: str = typer.Option(
            "main",
            "--default-session-id",
            help="Fallback session id for policy=main or missing context.",
        ),
    ) -> None:
        """Resolve one routing input against persisted binding rules."""

        settings = get_settings()
        try:
            decision = asyncio.run(
                get_channel_binding_service(settings).resolve(
                    routing_input=ChannelRoutingInput(
                        transport=transport,
                        account_id=account_id,
                        peer_id=peer_id,
                        thread_id=thread_id,
                        user_id=user_id,
                        default_session_id=default_session_id,
                    )
                )
            )
        except (ChannelBindingServiceError, ValueError) as exc:
            emit_profile_error(exc)
            raise typer.Exit(code=1) from None
        payload = {"decision": decision.model_dump(mode="json") if decision is not None else None}
        typer.echo(json.dumps(payload, ensure_ascii=True))
