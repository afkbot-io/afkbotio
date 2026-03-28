"""Channel operator CLI group registration."""

from __future__ import annotations

import json
from typing import NoReturn

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.inspection_shared import (
    ChannelInspectionSummary,
    build_channel_guardrail_summary,
    build_channel_inspection_summary,
    render_profile_memory_defaults_brief,
    render_memory_auto_save_brief,
    render_memory_auto_search_brief,
    render_merge_order_brief,
    render_tool_access_brief,
)
from afkbot.cli.commands.channel_telethon import register_telethon_commands
from afkbot.cli.commands.channel_telegram import register_telegram_commands
from afkbot.services.channels.endpoint_contracts import (
    ChannelEndpointConfig,
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
    deserialize_endpoint_config,
)
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointServiceError,
    run_channel_endpoint_service_sync,
)
from afkbot.services.profile_runtime import (
    ProfileDetails,
    ProfileServiceError,
    run_profile_service_sync,
)
from afkbot.settings import Settings, get_settings


def register(app: typer.Typer) -> None:
    """Register channel operator commands."""

    channel_app = typer.Typer(
        help="Operate external channel adapters and rollout probes.",
        no_args_is_help=True,
    )
    app.add_typer(channel_app, name="channel")

    @channel_app.command("list")
    def channel_list(
        transport: str | None = typer.Option(
            None,
            "--transport",
            help="Optional transport filter: telegram or telegram_user.",
        ),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """List all configured channel endpoints across transports."""

        settings = get_settings()
        try:
            channels = [
                _coerce_channel_endpoint(item)
                for item in run_channel_endpoint_service_sync(
                    settings,
                    lambda service: service.list(transport=transport),
                )
            ]
        except Exception as exc:
            _raise_channel_cli_error(exc)
        if json_output:
            typer.echo(json.dumps({"channels": [item.model_dump(mode="json") for item in channels]}, ensure_ascii=True))
            return
        if not channels:
            typer.echo("No channels configured.")
            return
        for item in channels:
            typer.echo(_render_channel_summary(item))

    @channel_app.command("show")
    def channel_show(
        channel_id: str = typer.Argument(..., help="Channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Show one channel endpoint regardless of transport family."""

        settings = get_settings()
        channel, profile, inspection = _load_channel_inspection(settings=settings, channel_id=channel_id)
        payload = {
            "channel": channel.model_dump(mode="json"),
            "profile": {"id": profile.id, "name": profile.name},
            "mutation_state": inspection.mutation_state.model_dump(mode="json"),
            "profile_ceiling": inspection.profile_ceiling.model_dump(mode="json"),
            "effective_permissions": inspection.effective_permissions.model_dump(mode="json"),
            "channel_guardrails": build_channel_guardrail_summary(inspection).model_dump(mode="json"),
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"Channel `{channel.endpoint_id}`")
        typer.echo(f"- transport: {channel.transport}")
        typer.echo(f"- adapter_kind: {channel.adapter_kind}")
        typer.echo(f"- profile: {channel.profile_id}")
        typer.echo(f"- credential_profile: {channel.credential_profile_key}")
        typer.echo(f"- account_id: {channel.account_id}")
        typer.echo(f"- enabled: {channel.enabled}")
        typer.echo(f"- merge_order: {render_merge_order_brief()}")
        typer.echo(f"- inherited_defaults_source: {inspection.mutation_state.inherited_defaults_source}")
        typer.echo(
            "- current_channel_overrides: "
            + (", ".join(inspection.mutation_state.current_override_fields) or "none")
        )
        typer.echo("- profile_ceiling_tool_access: " + render_tool_access_brief(inspection.profile_ceiling.tool_access))
        typer.echo(
            "- profile_memory_defaults: "
            + render_profile_memory_defaults_brief(inspection.profile_ceiling.memory_behavior)
        )
        typer.echo(f"- channel_tool_profile: {inspection.channel_tool_profile}")
        typer.echo(
            "- channel_tool_profile_allowlist: "
            + (
                ", ".join(inspection.channel_tool_profile_allowlist)
                if inspection.channel_tool_profile_allowlist
                else "inherit"
            )
        )
        typer.echo(f"- effective_workspace_root: {inspection.effective_permissions.default_workspace_root}")
        typer.echo(f"- effective_file_scope_mode: {inspection.effective_permissions.file_scope_mode}")
        typer.echo(f"- effective_file_access: {inspection.effective_permissions.file_access_mode}")
        typer.echo(f"- effective_network_access: {inspection.effective_permissions.network_access}")
        typer.echo("- effective_tool_access: " + render_tool_access_brief(inspection.effective_permissions.tool_access))
        typer.echo("- effective_memory_auto_search: " + render_memory_auto_search_brief(inspection.effective_permissions.memory_behavior))
        typer.echo("- effective_memory_auto_save: " + render_memory_auto_save_brief(inspection.effective_permissions.memory_behavior))
        typer.echo(
            "- effective_memory_cross_chat_access: "
            + inspection.effective_permissions.memory_behavior.explicit_cross_chat_access
        )
        typer.echo(
            f"- hard_blocked_tools: {', '.join(inspection.hard_blocked_tools) if inspection.hard_blocked_tools else 'none'}"
        )
        if isinstance(channel, TelegramPollingEndpointConfig):
            typer.echo(f"- group_trigger_mode: {channel.group_trigger_mode}")
            typer.echo(f"- ingress_batch: {_render_batch_brief(channel.ingress_batch.enabled)}")
        elif isinstance(channel, TelethonUserEndpointConfig):
            typer.echo(f"- reply_mode: {channel.reply_mode}")
            typer.echo(f"- group_invocation_mode: {channel.group_invocation_mode}")
            typer.echo(f"- watcher.enabled: {channel.watcher.enabled}")
            typer.echo(f"- ingress_batch: {_render_batch_brief(channel.ingress_batch.enabled)}")

    register_telegram_commands(channel_app)
    register_telethon_commands(channel_app)


def _render_channel_summary(channel: object) -> str:
    if isinstance(channel, TelegramPollingEndpointConfig):
        return (
            f"- {channel.endpoint_id}: transport={channel.transport}, profile={channel.profile_id}, "
            f"account_id={channel.account_id}, tool_profile={channel.tool_profile}, "
            f"mode={channel.group_trigger_mode}, enabled={channel.enabled}"
        )
    if isinstance(channel, TelethonUserEndpointConfig):
        return (
            f"- {channel.endpoint_id}: transport={channel.transport}, profile={channel.profile_id}, "
            f"account_id={channel.account_id}, tool_profile={channel.tool_profile}, reply_mode={channel.reply_mode}, "
            f"watcher={channel.watcher.enabled}, enabled={channel.enabled}"
        )
    return f"- {getattr(channel, 'endpoint_id', '?')}: transport={getattr(channel, 'transport', '?')}"


def _render_batch_brief(enabled: bool) -> str:
    return "enabled" if enabled else "disabled"


def _coerce_channel_endpoint(channel: ChannelEndpointConfig) -> ChannelEndpointConfig:
    """Coerce one generic endpoint payload into its transport-specific model when possible."""

    return deserialize_endpoint_config(channel.model_dump(mode="python"))


def _load_channel_inspection(
    *,
    settings: Settings,
    channel_id: str,
) -> tuple[ChannelEndpointConfig, ProfileDetails, ChannelInspectionSummary]:
    try:
        channel = _coerce_channel_endpoint(
            run_channel_endpoint_service_sync(
                settings,
                lambda service: service.get(endpoint_id=channel_id),
            )
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
        return channel, profile, inspection
    except Exception as exc:
        _raise_channel_cli_error(exc)
    raise AssertionError("unreachable")


def _raise_channel_cli_error(exc: Exception) -> NoReturn:
    error_code = getattr(exc, "error_code", None)
    reason = getattr(exc, "reason", str(exc))
    if isinstance(exc, (ChannelEndpointServiceError, ProfileServiceError)):
        raise_usage_error(f"ERROR [{error_code}] {reason}")
    raise_usage_error(str(exc))
