"""Read-only and operational Telethon channel CLI commands."""

from __future__ import annotations

import asyncio
import json

import typer

from afkbot.cli.commands.inspection_shared import (
    build_channel_inspection_summary,
    render_memory_auto_save_brief,
    render_memory_auto_search_brief,
    render_merge_order_brief,
    render_tool_access_brief,
)
from afkbot.cli.commands.channel_shared import (
    render_ingress_batch_summary,
    render_reply_humanization_summary,
)
from afkbot.cli.commands.channel_telethon_commands.legacy import (
    get_legacy_channel_endpoint_service,
    load_legacy_telethon_endpoint,
    raise_legacy_telethon_channel_error,
    reload_legacy_managed_runtime_notice,
)
from afkbot.cli.commands.channel_telethon_commands.watcher import render_watcher_list_summary
from afkbot.cli.commands.channel_telethon_runtime import (
    render_telethon_status_payload,
    set_telethon_endpoint_enabled,
    telethon_authorize_payload,
    telethon_dialogs_payload,
    telethon_logout_payload,
    telethon_reset_state_payload,
    telethon_status_payload,
)
from afkbot.services.channel_routing.service import (
    ChannelBindingServiceError,
    run_channel_binding_service_sync,
)
from afkbot.services.channels.endpoint_contracts import TelethonUserEndpointConfig
from afkbot.services.channels.telethon_user.runtime_support import evaluate_telethon_profile_policy
from afkbot.services.channels.telethon_user.watcher import resolve_watcher_delivery_target
from afkbot.services.profile_runtime import run_profile_service_sync
from afkbot.settings import get_settings


def register_telethon_query_commands(telethon_app: typer.Typer) -> None:
    """Register Telethon list/show/runtime commands under one Typer app."""

    @telethon_app.command("list")
    def telethon_list(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """List configured Telethon endpoints."""

        settings = get_settings()
        try:
            channels = asyncio.run(get_legacy_channel_endpoint_service(settings).list(transport="telegram_user"))
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        if json_output:
            typer.echo(
                json.dumps(
                    {"channels": [item.model_dump(mode="json") for item in channels]},
                    ensure_ascii=True,
                )
            )
            return
        if not channels:
            typer.echo("No Telethon channels configured.")
            return
        for item in channels:
            typed = TelethonUserEndpointConfig.model_validate(item.model_dump())
            typer.echo(
                f"- {typed.endpoint_id}: profile={typed.profile_id}, "
                f"credential_profile={typed.credential_profile_key}, account_id={typed.account_id}, "
                f"reply_mode={typed.reply_mode}, tool_profile={typed.tool_profile}, "
                f"group_invocation_mode={typed.group_invocation_mode}, "
                f"process_self_commands={typed.process_self_commands}, "
                f"ingress_batch={render_ingress_batch_summary(typed.ingress_batch)}, "
                f"reply_humanization={render_reply_humanization_summary(typed.reply_humanization)}, "
                f"watcher={render_watcher_list_summary(typed)}, enabled={typed.enabled}"
            )

    @telethon_app.command("show")
    def telethon_show(
        channel_id: str = typer.Argument(..., help="Telethon channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Show one Telethon endpoint."""

        settings = get_settings()
        try:
            channel = asyncio.run(load_legacy_telethon_endpoint(channel_id=channel_id))
            state_path = get_legacy_channel_endpoint_service(settings).telethon_user_state_path(
                endpoint_id=channel.endpoint_id
            )
            profile = run_profile_service_sync(
                settings,
                lambda service: service.get(profile_id=channel.profile_id),
            )
            inspection = build_channel_inspection_summary(
                settings=settings,
                profile=profile,
                channel=channel,
            )
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        payload = {
            "channel": channel.model_dump(mode="json"),
            "state_path": str(state_path),
            "state_present": state_path.exists(),
            "policy": {},
            "mutation_state": inspection.mutation_state.model_dump(mode="json"),
            "profile_ceiling": inspection.profile_ceiling.model_dump(mode="json"),
            "effective_permissions": inspection.effective_permissions.model_dump(mode="json"),
            "watcher_effective_delivery_target": resolve_watcher_delivery_target(
                account_id=channel.account_id,
                config=channel.watcher,
            ).model_dump(mode="json", exclude_none=True),
        }
        try:
            allowed, reason = asyncio.run(
                evaluate_telethon_profile_policy(
                    settings=settings,
                    profile_id=channel.profile_id,
                )
            )
            payload["policy"] = {"allows_runtime": allowed, "reason": reason}
        except Exception as exc:
            payload["policy"] = {"allows_runtime": False, "reason": str(exc)}
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"Telethon channel `{channel.endpoint_id}`")
        typer.echo(f"- profile: {channel.profile_id}")
        typer.echo(f"- credential_profile: {channel.credential_profile_key}")
        typer.echo(f"- account_id: {channel.account_id}")
        typer.echo(f"- merge_order: {render_merge_order_brief()}")
        typer.echo(f"- inherited_defaults_source: {inspection.mutation_state.inherited_defaults_source}")
        typer.echo(
            "- current_channel_overrides: "
            + (", ".join(inspection.mutation_state.current_override_fields) or "none")
        )
        typer.echo(
            "- profile_ceiling_tool_access: "
            + render_tool_access_brief(inspection.profile_ceiling.tool_access)
        )
        typer.echo(f"- reply_mode: {channel.reply_mode}")
        typer.echo(f"- tool_profile: {channel.tool_profile}")
        typer.echo("- reply.blocked_chat_patterns: " + (", ".join(channel.reply_blocked_chat_patterns) or "-"))
        typer.echo("- reply.allowed_chat_patterns: " + (", ".join(channel.reply_allowed_chat_patterns) or "-"))
        typer.echo(f"- group_invocation_mode: {channel.group_invocation_mode}")
        typer.echo(f"- process_self_commands: {channel.process_self_commands}")
        typer.echo(f"- command_prefix: {channel.command_prefix}")
        typer.echo(f"- ingress_batch.enabled: {channel.ingress_batch.enabled}")
        typer.echo(f"- ingress_batch.debounce_ms: {channel.ingress_batch.debounce_ms}")
        typer.echo(f"- ingress_batch.cooldown_sec: {channel.ingress_batch.cooldown_sec}")
        typer.echo(f"- ingress_batch.max_batch_size: {channel.ingress_batch.max_batch_size}")
        typer.echo(f"- ingress_batch.max_buffer_chars: {channel.ingress_batch.max_buffer_chars}")
        typer.echo(
            "- effective_memory_auto_search: "
            + render_memory_auto_search_brief(inspection.effective_permissions.memory_behavior)
        )
        typer.echo(
            "- effective_memory_auto_save: "
            + render_memory_auto_save_brief(inspection.effective_permissions.memory_behavior)
        )
        typer.echo(
            "- effective_memory_cross_chat_access: "
            + inspection.effective_permissions.memory_behavior.explicit_cross_chat_access
        )
        typer.echo(f"- reply_humanization.enabled: {channel.reply_humanization.enabled}")
        typer.echo(f"- reply_humanization.min_delay_ms: {channel.reply_humanization.min_delay_ms}")
        typer.echo(f"- reply_humanization.max_delay_ms: {channel.reply_humanization.max_delay_ms}")
        typer.echo(f"- reply_humanization.chars_per_second: {channel.reply_humanization.chars_per_second}")
        typer.echo(f"- mark_read_before_reply: {channel.mark_read_before_reply}")
        typer.echo(f"- watcher.enabled: {channel.watcher.enabled}")
        typer.echo(f"- watcher.unmuted_only: {channel.watcher.unmuted_only}")
        typer.echo(f"- watcher.include_private: {channel.watcher.include_private}")
        typer.echo(f"- watcher.include_groups: {channel.watcher.include_groups}")
        typer.echo(f"- watcher.include_channels: {channel.watcher.include_channels}")
        typer.echo(f"- watcher.batch_interval_sec: {channel.watcher.batch_interval_sec}")
        typer.echo(f"- watcher.dialog_refresh_interval_sec: {channel.watcher.dialog_refresh_interval_sec}")
        typer.echo(f"- watcher.max_batch_size: {channel.watcher.max_batch_size}")
        typer.echo(f"- watcher.max_buffer_size: {channel.watcher.max_buffer_size}")
        typer.echo(f"- watcher.max_message_chars: {channel.watcher.max_message_chars}")
        typer.echo("- watcher.blocked_chat_patterns: " + (", ".join(channel.watcher.blocked_chat_patterns) or "-"))
        typer.echo("- watcher.allowed_chat_patterns: " + (", ".join(channel.watcher.allowed_chat_patterns) or "-"))
        typer.echo(
            "- watcher.delivery_target: "
            + json.dumps(
                resolve_watcher_delivery_target(
                    account_id=channel.account_id,
                    config=channel.watcher,
                ).model_dump(exclude_none=True),
                ensure_ascii=True,
            )
        )
        typer.echo(
            f"- watcher.delivery_credential_profile: "
            f"{channel.watcher.delivery_credential_profile_key or '-'}"
        )
        policy = payload.get("policy")
        if isinstance(policy, dict):
            typer.echo(f"- policy.allows_runtime: {policy.get('allows_runtime')}")
            typer.echo(f"- policy.reason: {policy.get('reason') or '-'}")
        typer.echo(f"- enabled: {channel.enabled}")
        typer.echo(f"- state_path: {state_path}")
        typer.echo(f"- state_present: {state_path.exists()}")

    @telethon_app.command("dialogs")
    def telethon_dialogs(
        channel_id: str = typer.Argument(..., help="Telethon channel endpoint id."),
        query: str | None = typer.Option(
            None,
            "--query",
            help="Optional case-insensitive substring matched against title and username.",
        ),
        watched_only: bool = typer.Option(
            False,
            "--watched",
            help="Return only dialogs currently selected by the watcher config.",
        ),
        limit: int = typer.Option(50, "--limit", min=1, help="Maximum dialogs to return."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """List live Telethon dialogs and show reply/watcher filter matches."""

        try:
            payload = asyncio.run(
                telethon_dialogs_payload(
                    channel_id=channel_id,
                    query=query,
                    watched_only=watched_only,
                    limit=limit,
                )
            )
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        dialogs = payload.get("dialogs")
        if not isinstance(dialogs, list) or not dialogs:
            typer.echo("No dialogs matched.")
            return
        typer.echo(f"Dialogs: {len(dialogs)}")
        for item in dialogs:
            if not isinstance(item, dict):
                continue
            typer.echo(
                f"- {item.get('title')} "
                f"(chat_id={item.get('chat_id')}, kind={item.get('chat_kind')}, "
                f"username={item.get('username') or '-'}, muted={item.get('muted')}, "
                f"reply_match={item.get('reply_match')}, watcher_match={item.get('watcher_match')})"
            )

    @telethon_app.command("enable")
    def telethon_enable(
        channel_id: str = typer.Argument(..., help="Telethon channel endpoint id."),
    ) -> None:
        """Enable one Telethon endpoint."""

        set_telethon_endpoint_enabled(channel_id=channel_id, enabled=True)

    @telethon_app.command("disable")
    def telethon_disable(
        channel_id: str = typer.Argument(..., help="Telethon channel endpoint id."),
    ) -> None:
        """Disable one Telethon endpoint."""

        set_telethon_endpoint_enabled(channel_id=channel_id, enabled=False)

    @telethon_app.command("delete")
    def telethon_delete(
        channel_id: str = typer.Argument(..., help="Telethon channel endpoint id."),
        keep_binding: bool = typer.Option(
            False,
            "--keep-binding",
            help="Keep routing binding with the same id instead of deleting it too.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Delete one Telethon endpoint and its saved state."""

        settings = get_settings()
        try:
            asyncio.run(load_legacy_telethon_endpoint(channel_id=channel_id))
            asyncio.run(get_legacy_channel_endpoint_service(settings).delete(endpoint_id=channel_id))
            binding_removed = False
            if not keep_binding:
                try:
                    run_channel_binding_service_sync(
                        settings,
                        lambda service: service.delete(binding_id=channel_id),
                    )
                    binding_removed = True
                except ChannelBindingServiceError:
                    binding_removed = False
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        if json_output:
            typer.echo(json.dumps({"ok": True, "binding_removed": binding_removed}, ensure_ascii=True))
            reload_legacy_managed_runtime_notice(settings)
            return
        typer.echo(f"Telethon channel `{channel_id}` deleted.")
        if binding_removed:
            typer.echo(f"Matching binding `{channel_id}` deleted.")
        reload_legacy_managed_runtime_notice(settings)

    @telethon_app.command("status")
    def telethon_status(
        channel_id: str | None = typer.Argument(
            None,
            help="Optional Telethon channel endpoint id. When omitted, show all endpoints.",
        ),
        probe: bool = typer.Option(False, "--probe", help="Run live Telethon session probe."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Show Telethon endpoint status."""

        try:
            payload = asyncio.run(telethon_status_payload(channel_id=channel_id, probe=probe))
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
        else:
            render_telethon_status_payload(payload)
        if payload.get("ok") is False:
            raise typer.Exit(code=1)

    @telethon_app.command("authorize")
    def telethon_authorize(
        channel_id: str = typer.Argument(..., help="Telethon channel endpoint id."),
        qr: bool = typer.Option(False, "--qr", help="Authorize via QR instead of login code."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Run interactive Telethon authorization and save session_string credentials."""

        try:
            payload = asyncio.run(telethon_authorize_payload(channel_id=channel_id, qr=qr))
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            reload_legacy_managed_runtime_notice(get_settings())
            return
        typer.echo(
            f"Telethon channel `{channel_id}` authorized for user_id={payload['user_id']} "
            f"username={payload['username']} phone={payload['phone']} method={payload['method']}."
        )
        reload_legacy_managed_runtime_notice(get_settings())

    @telethon_app.command("logout")
    def telethon_logout(
        channel_id: str = typer.Argument(..., help="Telethon channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Log out one Telethon session and clear its stored session_string."""

        try:
            payload = asyncio.run(telethon_logout_payload(channel_id=channel_id))
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            reload_legacy_managed_runtime_notice(get_settings())
            return
        typer.echo(
            f"Telethon channel `{channel_id}` logout logged_out={payload['logged_out']} "
            f"network_logout_skipped={payload['network_logout_skipped']} "
            f"session_removed={payload['session_removed']}."
        )
        reload_legacy_managed_runtime_notice(get_settings())

    @telethon_app.command("reset-state")
    def telethon_reset_state(
        channel_id: str = typer.Argument(..., help="Telethon channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Reset saved Telethon runtime state."""

        try:
            payload = asyncio.run(telethon_reset_state_payload(channel_id=channel_id))
        except Exception as exc:
            raise_legacy_telethon_channel_error(exc)
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            reload_legacy_managed_runtime_notice(get_settings())
            return
        typer.echo(
            f"State for `{channel_id}` {'removed' if payload['removed'] else 'not found'}: {payload['state_path']}"
        )
        reload_legacy_managed_runtime_notice(get_settings())


__all__ = ["register_telethon_query_commands"]
