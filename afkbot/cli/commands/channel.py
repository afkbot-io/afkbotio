"""Channel operator CLI group registration."""

from __future__ import annotations

import json
import sys
from typing import NoReturn

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.managed_runtime import reload_install_managed_runtime_notice
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
from afkbot.cli.commands.channel_partyflow import register_partyflow_commands
from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_int,
    resolve_channel_text,
)
from afkbot.cli.commands.channel_shared import split_channel_access_list
from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg, resolve_prompt_language
from afkbot.services.channels.endpoint_contracts import (
    ChannelAccessPolicy,
    ChannelEndpointConfig,
    PartyFlowPollingEndpointConfig,
    TelegramPollingEndpointConfig,
    TelethonUserEndpointConfig,
    deserialize_endpoint_config,
)
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointService,
    ChannelEndpointServiceError,
    run_channel_endpoint_service_sync,
)
from afkbot.services.plugins import PluginServiceError, get_plugin_service
from afkbot.services.config_schema import JsonConfigField, normalize_json_config_fields
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
            help="Optional transport filter: telegram, telegram_user, or partyflow.",
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
            typer.echo(
                json.dumps(
                    {"channels": [item.model_dump(mode="json") for item in channels]},
                    ensure_ascii=True,
                )
            )
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
        channel, profile, inspection = _load_channel_inspection(
            settings=settings, channel_id=channel_id
        )
        payload = {
            "channel": channel.model_dump(mode="json"),
            "profile": {"id": profile.id, "name": profile.name},
            "mutation_state": inspection.mutation_state.model_dump(mode="json"),
            "profile_ceiling": inspection.profile_ceiling.model_dump(mode="json"),
            "effective_permissions": inspection.effective_permissions.model_dump(mode="json"),
            "channel_guardrails": build_channel_guardrail_summary(inspection).model_dump(
                mode="json"
            ),
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
        typer.echo(
            f"- inherited_defaults_source: {inspection.mutation_state.inherited_defaults_source}"
        )
        typer.echo(
            "- current_channel_overrides: "
            + (", ".join(inspection.mutation_state.current_override_fields) or "none")
        )
        typer.echo(
            "- profile_ceiling_tool_access: "
            + render_tool_access_brief(inspection.profile_ceiling.tool_access)
        )
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
        typer.echo(
            f"- effective_workspace_root: {inspection.effective_permissions.default_workspace_root}"
        )
        typer.echo(
            f"- effective_shell_sandbox_mode: {inspection.effective_permissions.shell_sandbox_mode}"
        )
        typer.echo(
            f"- effective_file_scope_mode: {inspection.effective_permissions.file_scope_mode}"
        )
        typer.echo(f"- effective_file_access: {inspection.effective_permissions.file_access_mode}")
        typer.echo(f"- effective_network_access: {inspection.effective_permissions.network_access}")
        typer.echo(
            "- effective_tool_access: "
            + render_tool_access_brief(inspection.effective_permissions.tool_access)
        )
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
        elif isinstance(channel, PartyFlowPollingEndpointConfig):
            typer.echo("- delivery_mode: poll")
            typer.echo(f"- trigger_mode: {channel.trigger_mode}")
            typer.echo("- trigger_keywords: " + (", ".join(channel.trigger_keywords) or "-"))
            typer.echo(f"- reply_mode: {channel.reply_mode}")
            typer.echo(f"- ingress_batch: {_render_batch_brief(channel.ingress_batch.enabled)}")

    @channel_app.command("enable")
    def channel_enable(
        channel_id: str = typer.Argument(..., help="Channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Enable one channel endpoint regardless of transport family."""

        _set_channel_enabled(channel_id=channel_id, enabled=True, json_output=json_output)

    @channel_app.command("disable")
    def channel_disable(
        channel_id: str = typer.Argument(..., help="Channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Disable one channel endpoint regardless of transport family."""

        _set_channel_enabled(channel_id=channel_id, enabled=False, json_output=json_output)

    @channel_app.command("delete")
    def channel_delete(
        channel_id: str = typer.Argument(..., help="Channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Delete one channel endpoint regardless of transport family."""

        settings = get_settings()
        try:
            run_channel_endpoint_service_sync(
                settings,
                lambda service: service.delete(endpoint_id=channel_id),
            )
        except Exception as exc:
            _raise_channel_cli_error(exc)
        reload_install_managed_runtime_notice(settings)
        if json_output:
            typer.echo(json.dumps({"deleted": True, "channel_id": channel_id}, ensure_ascii=True))
            return
        typer.echo(f"Channel `{channel_id}` deleted.")

    plugin_app = typer.Typer(
        help="Generic controls for plugin-provided channel adapters.",
        no_args_is_help=True,
    )
    channel_app.add_typer(plugin_app, name="plugin")

    @plugin_app.command("adapters")
    def plugin_channel_adapters(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """List active plugin-provided channel adapters."""

        settings = get_settings()
        try:
            adapters = get_plugin_service(settings).channel_adapters()
        except Exception as exc:
            _raise_channel_cli_error(exc)
        payload = [
            {
                "transport": adapter.transport,
                "adapter_kind": adapter.adapter_kind,
                "label": adapter.label,
                "description": adapter.description,
                "setup_instructions": adapter.setup_instructions,
                "endpoint_config_schema": dict(adapter.endpoint_config_schema),
                "supports_runtime": adapter.build_runtime is not None,
                "supports_send": adapter.send_message is not None,
                "validates_endpoint_config": adapter.validate_endpoint_config is not None,
                "validates_target": adapter.validate_target is not None,
                "outbound_target_key": adapter.outbound_target_key is not None,
            }
            for adapter in sorted(adapters.values(), key=lambda item: (item.transport, item.adapter_kind))
        ]
        if json_output:
            typer.echo(json.dumps({"adapters": payload}, ensure_ascii=True))
            return
        if not payload:
            typer.echo("No plugin channel adapters enabled.")
            return
        typer.echo(f"Plugin channel adapters: {len(payload)}")
        for item in payload:
            typer.echo(
                "- "
                f"{item['transport']}/{item['adapter_kind']}: "
                f"runtime={item['supports_runtime']}, send={item['supports_send']}, "
                f"label={item['label'] or '-'}"
            )

    @plugin_app.command("add")
    def plugin_channel_add(
        channel_id: str = typer.Argument(..., help="Stable channel endpoint id."),
        transport: str = typer.Option(..., "--transport", help="Plugin channel transport id."),
        adapter_kind: str = typer.Option(
            ...,
            "--adapter-kind",
            help="Plugin channel adapter kind.",
        ),
        profile_id: str = typer.Option("default", "--profile", help="Target profile-agent id."),
        credential_profile_key: str = typer.Option(
            ...,
            "--credential-profile",
            help="Credential profile key used by the plugin channel.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Logical account id. Defaults to channel id.",
        ),
        enabled: bool = typer.Option(
            True,
            "--enabled/--disabled",
            help="Enable or disable this endpoint.",
        ),
        tool_profile: str = typer.Option(
            "messaging_safe",
            "--tool-profile",
            help="Channel tool profile for routed turns.",
        ),
        private_policy: str = typer.Option(
            "allowlist",
            "--private-policy",
            help="Private access policy: open, allowlist, disabled.",
        ),
        allow_from: str | None = typer.Option(
            None,
            "--allow-from",
            help="Comma-separated private sender ids.",
        ),
        group_policy: str = typer.Option(
            "disabled",
            "--group-policy",
            help="Group access policy: open, allowlist, disabled.",
        ),
        groups: str | None = typer.Option(None, "--groups", help="Comma-separated group ids."),
        group_allow_from: str | None = typer.Option(
            None,
            "--group-allow-from",
            help="Comma-separated group sender ids.",
        ),
        outbound_allow_to: str | None = typer.Option(
            None,
            "--outbound-allow-to",
            help="Comma-separated outbound target ids allowed for channel.send.",
        ),
        config_json: str | None = typer.Option(
            None,
            "--config-json",
            help=(
                "Adapter-specific endpoint config JSON object. Omit it to use adapter defaults "
                "or answer schema-driven prompts in an interactive terminal."
            ),
        ),
        yes: bool = typer.Option(False, "--yes", help="Use adapter defaults; do not prompt."),
        lang: str | None = typer.Option(None, "--lang", help="Interactive language: en or ru."),
        ru: bool = typer.Option(False, "--ru", help="Shortcut for --lang ru in interactive mode."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        """Create one endpoint for a plugin-provided channel adapter."""

        settings = get_settings()
        prompt_language = resolve_prompt_language(settings=settings, value=lang, ru=ru)
        try:
            adapters = get_plugin_service(settings).channel_adapters()
            adapter = adapters.get((transport.strip().lower(), adapter_kind.strip().lower()))
            if adapter is None:
                raise_usage_error(
                    "Plugin channel adapter is not installed and enabled: "
                    f"transport={transport.strip().lower()} adapter_kind={adapter_kind.strip().lower()}. "
                    "Install/enable the plugin first, then run `afk channel plugin adapters`."
                )
            config_payload = _resolve_plugin_channel_config_payload(
                adapter=adapter,
                config_json=config_json,
                yes=yes,
                lang=prompt_language,
            )
            endpoint = ChannelEndpointConfig(
                endpoint_id=channel_id,
                transport=transport,
                adapter_kind=adapter_kind,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
                account_id=account_id or channel_id,
                enabled=enabled,
                tool_profile=tool_profile,
                access_policy=ChannelAccessPolicy(
                    private_policy=private_policy,
                    allow_from=split_channel_access_list(allow_from),
                    group_policy=group_policy,
                    groups=split_channel_access_list(groups),
                    group_allow_from=split_channel_access_list(group_allow_from),
                    outbound_allow_to=split_channel_access_list(outbound_allow_to),
                ),
                config=config_payload,
            )
            endpoint = adapter.validate_config_schema_payload(endpoint)
            if adapter.validate_endpoint_config is not None:
                endpoint = adapter.validate_endpoint_config(endpoint)
            created = run_channel_endpoint_service_sync(
                settings,
                lambda service: service.create(endpoint),
            )
        except typer.Exit:
            raise
        except json.JSONDecodeError as exc:
            raise_usage_error(f"--config-json must be valid JSON: {exc.msg}")
        except Exception as exc:
            _raise_channel_cli_error(exc)
        reload_install_managed_runtime_notice(settings)
        if json_output:
            typer.echo(json.dumps({"channel": created.model_dump(mode="json")}, ensure_ascii=True))
            return
        typer.echo(f"Plugin channel `{created.endpoint_id}` created.")
        typer.echo(f"- transport: {created.transport}")
        typer.echo(f"- adapter_kind: {created.adapter_kind}")
        typer.echo(f"- profile: {created.profile_id}")
        typer.echo(f"- credential_profile: {created.credential_profile_key}")
        typer.echo(f"- account_id: {created.account_id}")
        typer.echo(f"- enabled: {created.enabled}")
        if created.config:
            typer.echo(f"- config: {json.dumps(created.config, ensure_ascii=True)}")

    register_telegram_commands(channel_app)
    register_telethon_commands(channel_app)
    register_partyflow_commands(channel_app)


def _set_channel_enabled(
    *,
    channel_id: str,
    enabled: bool,
    json_output: bool,
) -> None:
    settings = get_settings()
    try:
        updated = run_channel_endpoint_service_sync(
            settings,
            lambda service: _update_channel_enabled(service, channel_id=channel_id, enabled=enabled),
        )
    except Exception as exc:
        _raise_channel_cli_error(exc)
    reload_install_managed_runtime_notice(settings)
    if json_output:
        typer.echo(json.dumps({"channel": updated.model_dump(mode="json")}, ensure_ascii=True))
        return
    state = "enabled" if updated.enabled else "disabled"
    typer.echo(f"Channel `{updated.endpoint_id}` {state}.")


async def _update_channel_enabled(
    service: ChannelEndpointService,
    *,
    channel_id: str,
    enabled: bool,
) -> ChannelEndpointConfig:
    endpoint = await service.get(endpoint_id=channel_id)
    updated = endpoint.model_copy(update={"enabled": enabled})
    return await service.update(updated)


def _resolve_plugin_channel_config_payload(
    *,
    adapter: object,
    config_json: str | None,
    yes: bool,
    lang: PromptLanguage,
) -> dict[str, object]:
    raw_payload: object = {} if config_json is None else json.loads(config_json)
    if not isinstance(raw_payload, dict):
        raise_usage_error("--config-json must be a JSON object.")
    schema_fields = normalize_json_config_fields(getattr(adapter, "endpoint_config_schema", {}))
    if (
        config_json is None
        and schema_fields
        and not yes
        and sys.stdin.isatty()
        and sys.stdout.isatty()
    ):
        return _collect_plugin_channel_config_inputs(schema_fields=schema_fields, lang=lang)
    return dict(raw_payload)


def _collect_plugin_channel_config_inputs(
    *,
    schema_fields: dict[str, JsonConfigField],
    lang: PromptLanguage,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    typer.echo(
        msg(
            lang,
            en="Plugin channel adapter config",
            ru="Конфигурация plugin-канала",
        )
    )
    for key, field in schema_fields.items():
        label = field.title or key
        description = field.description or f"Set `{key}`."
        if field.choices:
            default_choice = (
                str(field.default)
                if isinstance(field.default, str) and field.default in field.choices
                else field.choices[0]
            )
            payload[key] = resolve_channel_choice(
                value=None,
                interactive=True,
                prompt_en=label,
                prompt_ru=label,
                default=default_choice,
                allowed=field.choices,
                lang=lang,
                detail_en=description,
                detail_ru=description,
            )
            continue
        if field.type == "boolean":
            payload[key] = resolve_channel_bool(
                value=None,
                interactive=True,
                prompt_en=label,
                prompt_ru=label,
                default=field.default if isinstance(field.default, bool) else False,
                lang=lang,
                detail_en=description,
                detail_ru=description,
            )
            continue
        if field.type == "integer":
            default_int = field.default if isinstance(field.default, int) else 0
            payload[key] = resolve_channel_int(
                value=None,
                interactive=True,
                prompt_en=label,
                prompt_ru=label,
                default=default_int,
                lang=lang,
                min_value=int(field.minimum) if field.minimum is not None else None,
                max_value=int(field.maximum) if field.maximum is not None else None,
                detail_en=description,
                detail_ru=description,
            )
            continue
        if field.type == "number":
            if field.description:
                typer.echo(field.description)
            default_number = field.default if isinstance(field.default, (int, float)) else 0
            payload[key] = float(typer.prompt(label, default=default_number, type=float))
            continue
        payload[key] = _collect_plugin_channel_text_config(key=key, field=field, lang=lang)
    return payload


def _collect_plugin_channel_text_config(
    *,
    key: str,
    field: JsonConfigField,
    lang: PromptLanguage,
) -> str:
    label = field.title or key
    description = field.description or f"Set `{key}`."
    if field.secret:
        if description:
            typer.echo(description)
        return str(typer.prompt(label, hide_input=True)).strip()
    default = field.default if isinstance(field.default, str) else None
    return resolve_channel_text(
        value=None,
        interactive=True,
        prompt_en=label,
        prompt_ru=label,
        default=default,
        lang=lang,
        allow_empty=not field.required,
        detail_en=description,
        detail_ru=description,
    )


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
    if isinstance(channel, PartyFlowPollingEndpointConfig):
        return (
            f"- {channel.endpoint_id}: transport={channel.transport}, profile={channel.profile_id}, "
            f"account_id={channel.account_id}, tool_profile={channel.tool_profile}, trigger_mode={channel.trigger_mode}, "
            f"trigger_keywords={','.join(channel.trigger_keywords) or '-'}, "
            f"reply_mode={channel.reply_mode}, enabled={channel.enabled}"
        )
    return (
        f"- {getattr(channel, 'endpoint_id', '?')}: transport={getattr(channel, 'transport', '?')}"
    )


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
    if isinstance(exc, (ChannelEndpointServiceError, ProfileServiceError, PluginServiceError)):
        raise_usage_error(f"ERROR [{error_code}] {reason}")
    raise_usage_error(str(exc))
