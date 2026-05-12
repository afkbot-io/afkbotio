"""PartyFlow polling channel CLI registration and operations."""

from __future__ import annotations

import asyncio
import json
from typing import cast

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.channel_credentials_support import configure_partyflow_channel_credentials
from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_int,
    resolve_channel_setup_scenario,
    resolve_channel_text,
)
from afkbot.cli.commands.channel_shared import (
    build_generated_channel_id,
    build_ingress_batch_config,
    collect_channel_access_policy_inputs,
    collect_channel_add_base_inputs,
    load_channel_profile,
    merge_ingress_batch_config,
    normalize_channel_tool_profile,
    put_access_policy_bindings,
    resolve_binding_update_inputs,
    resolve_channel_update_profile_id,
    render_channel_add_intro,
    render_ingress_batch_summary,
    should_collect_channel_add_interactively,
    should_collect_channel_update_interactively,
)
from afkbot.cli.commands.inspection_shared import (
    build_channel_inspection_summary,
    render_memory_auto_save_brief,
    render_memory_auto_search_brief,
    render_merge_order_brief,
    render_tool_access_brief,
)
from afkbot.cli.managed_runtime import reload_install_managed_runtime_notice
from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg
from afkbot.cli.presentation.setup_prompts import resolve_prompt_language
from afkbot.services.apps.partyflow.http_api import (
    PARTYFLOW_API_BASE_URL,
    PartyFlowApiError,
    _get_me,
)
from afkbot.services.channel_routing import ChannelBindingRule, ChannelBindingService
from afkbot.services.channel_routing.contracts import SessionPolicy
from afkbot.services.channel_routing.service import (
    ChannelBindingServiceError,
    get_channel_binding_service,
    run_channel_binding_service_sync,
)
from afkbot.services.channels.endpoint_contracts import (
    CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
    CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
    CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
    CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
    CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
    CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
    CHANNEL_INGRESS_BATCH_SIZE_MAX,
    CHANNEL_INGRESS_BATCH_SIZE_MIN,
    ChannelEndpointConfig,
    PartyFlowPollingEndpointConfig,
    UNSUPPORTED_PARTYFLOW_WEBHOOK_REASON,
)
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointServiceError,
    get_channel_endpoint_service,
    partyflow_polling_state_path_for,
    run_channel_endpoint_service_sync,
)
from afkbot.services.channels.partyflow_polling import PartyFlowPollingService
from afkbot.services.channels.tool_profiles import (
    CHANNEL_TOOL_PROFILE_HELP,
    CHANNEL_TOOL_PROFILE_VALUES,
)
from afkbot.services.credentials import CredentialsServiceError, get_credentials_service
from afkbot.services.profile_runtime import ProfileDetails, run_profile_service_sync
from afkbot.services.wizard.preview import (
    build_channel_surface_preview,
    current_channel_tool_names_for_transport,
)
from afkbot.settings import Settings, get_settings

_PARTYFLOW_TRIGGER_MODES = ("all", "mention", "keywords")
_PARTYFLOW_REPLY_MODES = ("same_conversation", "disabled")
_PARTYFLOW_BOT_TOKEN = "partyflow_bot_token"


def register_partyflow_commands(channel_app: typer.Typer) -> None:
    """Register PartyFlow channel controls under `afk channel partyflow`."""

    partyflow_app = typer.Typer(help="PartyFlow polling controls.", no_args_is_help=True)
    channel_app.add_typer(partyflow_app, name="partyflow")

    @partyflow_app.command("add")
    def partyflow_add(
        channel_id: str | None = typer.Argument(
            None,
            help="Stable channel id used later in show/delete commands.",
        ),
        profile_id: str | None = typer.Option(None, "--profile", help="Target profile-agent id."),
        credential_profile_key: str | None = typer.Option(
            None,
            "--credential-profile",
            help="PartyFlow credential profile key holding the bot token. Defaults to channel id.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Logical account id used by bindings and routing telemetry. Defaults to channel id.",
        ),
        enabled: bool | None = typer.Option(
            None, "--enabled/--disabled", help="Enable or disable this endpoint."
        ),
        trigger_mode: str | None = typer.Option(
            None,
            "--trigger-mode",
            help="Polling trigger mode: all, mention, keywords.",
        ),
        trigger_keywords: str | None = typer.Option(
            None,
            "--trigger-keywords",
            help="Comma-separated keywords used when --trigger-mode keywords is selected.",
        ),
        private_policy: str | None = typer.Option(
            None,
            "--private-policy",
            help="Private conversation access: open, allowlist, disabled.",
            case_sensitive=False,
        ),
        allow_from: str | None = typer.Option(
            None,
            "--allow-from",
            help="Comma-separated PartyFlow user ids allowed in private allowlist mode.",
        ),
        group_policy: str | None = typer.Option(
            None,
            "--group-policy",
            help="Group/channel access: open, allowlist, disabled.",
            case_sensitive=False,
        ),
        groups: str | None = typer.Option(
            None,
            "--groups",
            help="Comma-separated PartyFlow conversation ids allowed in group allowlist mode.",
        ),
        group_allow_from: str | None = typer.Option(
            None,
            "--group-allow-from",
            help="Comma-separated PartyFlow user ids allowed to trigger AFKBOT in allowed groups.",
        ),
        outbound_allow_to: str | None = typer.Option(
            None,
            "--outbound-allow-to",
            help="Comma-separated PartyFlow conversation ids this endpoint may send to.",
        ),
        reply_mode: str | None = typer.Option(
            None,
            "--reply-mode",
            help="Reply behavior: same_conversation or disabled.",
        ),
        tool_profile: str | None = typer.Option(
            None,
            "--tool-profile",
            help=CHANNEL_TOOL_PROFILE_HELP,
            case_sensitive=False,
        ),
        ingress_batch_enabled: bool | None = typer.Option(
            None,
            "--ingress-batch-enabled/--no-ingress-batch-enabled",
            help="Delay and coalesce sequential inbound polling messages before one turn.",
        ),
        ingress_debounce_ms: int | None = typer.Option(None, "--ingress-debounce-ms"),
        ingress_cooldown_sec: int | None = typer.Option(None, "--ingress-cooldown-sec"),
        ingress_max_batch_size: int | None = typer.Option(None, "--ingress-max-batch-size"),
        ingress_max_buffer_chars: int | None = typer.Option(None, "--ingress-max-buffer-chars"),
        create_binding: bool | None = typer.Option(
            None,
            "--binding/--no-binding",
            help="Create/update matching routing binding; --no-binding keeps any existing one.",
        ),
        session_policy: SessionPolicy | None = typer.Option(
            None,
            "--session-policy",
            help="Binding session policy when --binding is enabled.",
        ),
        prompt_overlay: str | None = typer.Option(
            None,
            "--prompt-overlay",
            help="Optional routing prompt overlay applied through the matching binding.",
        ),
        priority: int = typer.Option(0, "--priority"),
        yes: bool = typer.Option(False, "--yes"),
        lang: str | None = typer.Option(None, "--lang", help="Interactive language: en or ru."),
        ru: bool = typer.Option(False, "--ru", help="Shortcut for --lang ru in interactive mode."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        prompt_language = resolve_prompt_language(settings=settings, value=lang, ru=ru)
        generated_channel_id = build_generated_channel_id(transport="partyflow")
        interactive = should_collect_channel_add_interactively(
            yes=yes,
            channel_id=channel_id,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
        )
        try:
            if interactive:
                render_channel_add_intro(
                    transport="partyflow",
                    lang=prompt_language,
                    suggested_channel_id=generated_channel_id,
                )
            scenario = resolve_channel_setup_scenario(
                transport="partyflow",
                interactive=interactive,
                lang=prompt_language,
            )
            base_inputs = collect_channel_add_base_inputs(
                settings=settings,
                interactive=interactive,
                lang=prompt_language,
                channel_id=channel_id,
                profile_id=profile_id,
                credential_profile_key=credential_profile_key,
                account_id=account_id,
                enabled=enabled,
                tool_profile=tool_profile or (scenario.tool_profile if scenario else None),
                create_binding=create_binding,
                session_policy=session_policy,
                binding_session_policy_default=cast(
                    SessionPolicy,
                    scenario.session_policy if scenario else "per-thread",
                ),
                binding_session_policy_allowed=("main", "per-chat", "per-thread", "per-user-in-group"),
                generated_channel_id=generated_channel_id,
            )
            resolved_trigger_mode = _resolve_trigger_mode(
                value=trigger_mode or (scenario.trigger_mode if scenario else None),
                interactive=interactive,
                lang=prompt_language,
                default="mention",
            )
            resolved_ingress_enabled = resolve_channel_bool(
                value=ingress_batch_enabled,
                interactive=interactive,
                prompt_en="Merge message bursts before replying?",
                prompt_ru="Объединять всплески сообщений перед ответом?",
                default=False,
                lang=prompt_language,
                detail_en=(
                    "When enabled, AFKBOT waits briefly after new PartyFlow messages and sends "
                    "one combined prompt to the agent."
                ),
                detail_ru=(
                    "Если включить, AFKBOT коротко ждёт после новых сообщений PartyFlow и "
                    "отправляет агенту один объединённый запрос."
                ),
            )
            endpoint = PartyFlowPollingEndpointConfig(
                endpoint_id=base_inputs.channel_id,
                profile_id=base_inputs.profile_id,
                credential_profile_key=base_inputs.credential_profile_key,
                account_id=base_inputs.account_id,
                enabled=base_inputs.enabled,
                trigger_mode=resolved_trigger_mode,  # type: ignore[arg-type]
                trigger_keywords=_resolve_trigger_keywords(
                    interactive=interactive,
                    lang=prompt_language,
                    trigger_mode=resolved_trigger_mode,
                    trigger_keywords=trigger_keywords,
                    current_trigger_mode="",
                    current_trigger_keywords=(),
                ),
                reply_mode=resolve_channel_choice(
                    value=reply_mode or (scenario.reply_mode if scenario else None),
                    interactive=interactive,
                    prompt_en="PartyFlow reply mode",
                    prompt_ru="Режим ответа PartyFlow",
                    default="same_conversation",
                    allowed=_PARTYFLOW_REPLY_MODES,
                    lang=prompt_language,
                ),  # type: ignore[arg-type]
                tool_profile=base_inputs.tool_profile,
                access_policy=collect_channel_access_policy_inputs(
                    interactive=interactive,
                    lang=prompt_language,
                    private_policy=private_policy or (scenario.private_policy if scenario else None),
                    allow_from=allow_from,
                    group_policy=group_policy or (scenario.group_policy if scenario else None),
                    groups=groups,
                    group_allow_from=group_allow_from,
                    outbound_allow_to=outbound_allow_to,
                    tool_profile=base_inputs.tool_profile,
                    private_policy_default="disabled",
                ),
                ingress_batch=build_ingress_batch_config(
                    enabled=resolved_ingress_enabled,
                    debounce_ms=resolve_channel_int(
                        value=ingress_debounce_ms,
                        interactive=interactive and resolved_ingress_enabled,
                        prompt_en="Quiet window before merge (ms)",
                        prompt_ru="Окно тишины перед объединением (мс)",
                        default=1500,
                        lang=prompt_language,
                        min_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
                        max_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
                    ),
                    cooldown_sec=resolve_channel_int(
                        value=ingress_cooldown_sec,
                        interactive=interactive and resolved_ingress_enabled,
                        prompt_en="Pause after each merged turn (sec)",
                        prompt_ru="Пауза после каждого объединённого хода (сек)",
                        default=0,
                        lang=prompt_language,
                        min_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
                        max_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
                    ),
                    max_batch_size=resolve_channel_int(
                        value=ingress_max_batch_size,
                        interactive=interactive and resolved_ingress_enabled,
                        prompt_en="Maximum messages per merged turn",
                        prompt_ru="Максимум сообщений в одном объединённом ходе",
                        default=20,
                        lang=prompt_language,
                        min_value=CHANNEL_INGRESS_BATCH_SIZE_MIN,
                        max_value=CHANNEL_INGRESS_BATCH_SIZE_MAX,
                    ),
                    max_buffer_chars=resolve_channel_int(
                        value=ingress_max_buffer_chars,
                        interactive=interactive and resolved_ingress_enabled,
                        prompt_en="Maximum merged text size (chars)",
                        prompt_ru="Максимальный размер объединённого текста (символы)",
                        default=12000,
                        lang=prompt_language,
                        min_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
                        max_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
                    ),
                ),
            )
            if interactive and credential_profile_key is None:
                configure_partyflow_channel_credentials(
                    settings=settings,
                    profile_id=base_inputs.profile_id,
                    credential_profile_key=base_inputs.credential_profile_key,
                    interactive=True,
                    lang=prompt_language,
                )
            saved = _create_partyflow_endpoint(settings=settings, endpoint=endpoint)
            binding_count = 0
            if base_inputs.create_binding:
                binding_count = put_access_policy_bindings(
                    settings=settings,
                    endpoint_id=saved.endpoint_id,
                    transport=saved.transport,
                    profile_id=saved.profile_id,
                    session_policy=base_inputs.session_policy,
                    priority=priority,
                    enabled=saved.enabled,
                    account_id=saved.account_id,
                    prompt_overlay=prompt_overlay,
                    access_policy=saved.access_policy,
                    replace_existing=True,
                )
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        if json_output:
            typer.echo(json.dumps({"channel": saved.model_dump(mode="json")}, ensure_ascii=True))
            reload_install_managed_runtime_notice(settings)
            return
        typer.echo(
            msg(
                prompt_language,
                en=(
                    "PartyFlow polling channel created "
                    f"(id={saved.endpoint_id}, trigger_mode={saved.trigger_mode}, "
                    f"ingress_batch={saved.ingress_batch.enabled}, reply_mode={saved.reply_mode})."
                ),
                ru=(
                    "Polling-канал PartyFlow создан "
                    f"(id={saved.endpoint_id}, trigger_mode={saved.trigger_mode}, "
                    f"ingress_batch={saved.ingress_batch.enabled}, reply_mode={saved.reply_mode})."
                ),
            )
        )
        if saved.trigger_keywords:
            typer.echo(f"- trigger_keywords: {', '.join(saved.trigger_keywords)}")
        if base_inputs.create_binding:
            typer.echo(f"- matching_bindings: {binding_count}")
        for line in build_channel_surface_preview(
            transport=saved.transport,
            scenario_id=None if scenario is None else scenario.id,
            tool_profile=saved.tool_profile,
            trigger_mode=saved.trigger_mode,
            reply_mode=saved.reply_mode,
            private_policy=saved.access_policy.private_policy,
            group_policy=saved.access_policy.group_policy,
            current_channel_tools=current_channel_tool_names_for_transport(saved.transport),
            credential_status=("bot_token_configured_or_prompted",),
            lang=prompt_language,
        ).lines:
            typer.echo(f"- {line}" if not line.startswith("-") else line)
        _render_access_policy(saved)
        typer.echo("- configure PartyFlow bot event_delivery_mode: poll")
        typer.echo("- configure PartyFlow bot event_types: MESSAGE_CREATED")
        typer.echo(
            f"- verify credentials with `afk channel partyflow status {saved.endpoint_id} --probe`."
        )
        reload_install_managed_runtime_notice(settings)

    @partyflow_app.command("update")
    def partyflow_update(
        channel_id: str = typer.Argument(...),
        profile_id: str | None = typer.Option(None, "--profile"),
        credential_profile_key: str | None = typer.Option(None, "--credential-profile"),
        account_id: str | None = typer.Option(None, "--account-id"),
        trigger_mode: str | None = typer.Option(None, "--trigger-mode"),
        trigger_keywords: str | None = typer.Option(None, "--trigger-keywords"),
        private_policy: str | None = typer.Option(None, "--private-policy", case_sensitive=False),
        allow_from: str | None = typer.Option(None, "--allow-from"),
        group_policy: str | None = typer.Option(None, "--group-policy", case_sensitive=False),
        groups: str | None = typer.Option(None, "--groups"),
        group_allow_from: str | None = typer.Option(None, "--group-allow-from"),
        outbound_allow_to: str | None = typer.Option(None, "--outbound-allow-to"),
        reply_mode: str | None = typer.Option(None, "--reply-mode"),
        tool_profile: str | None = typer.Option(None, "--tool-profile", case_sensitive=False),
        ingress_batch_enabled: bool | None = typer.Option(
            None,
            "--ingress-batch-enabled/--no-ingress-batch-enabled",
        ),
        ingress_debounce_ms: int | None = typer.Option(None, "--ingress-debounce-ms"),
        ingress_cooldown_sec: int | None = typer.Option(None, "--ingress-cooldown-sec"),
        ingress_max_batch_size: int | None = typer.Option(None, "--ingress-max-batch-size"),
        ingress_max_buffer_chars: int | None = typer.Option(None, "--ingress-max-buffer-chars"),
        yes: bool = typer.Option(False, "--yes"),
        lang: str | None = typer.Option(None, "--lang"),
        ru: bool = typer.Option(False, "--ru"),
        sync_binding: bool = typer.Option(False, "--binding"),
        session_policy: SessionPolicy | None = typer.Option(None, "--session-policy"),
        prompt_overlay: str | None = typer.Option(None, "--prompt-overlay"),
        priority: int | None = typer.Option(None, "--priority"),
        json_output: bool = typer.Option(False, "--json"),
    ) -> None:
        settings = get_settings()
        binding_count = 0
        try:
            current = _load_partyflow_endpoint(settings=settings, channel_id=channel_id)
            prompt_language = resolve_prompt_language(settings=settings, value=lang, ru=ru)
            interactive = should_collect_channel_update_interactively(
                yes=yes,
                sync_binding=sync_binding,
                values=(
                    profile_id,
                    credential_profile_key,
                    account_id,
                    trigger_mode,
                    trigger_keywords,
                    private_policy,
                    allow_from,
                    group_policy,
                    groups,
                    group_allow_from,
                    outbound_allow_to,
                    reply_mode,
                    tool_profile,
                    ingress_batch_enabled,
                    ingress_debounce_ms,
                    ingress_cooldown_sec,
                    ingress_max_batch_size,
                    ingress_max_buffer_chars,
                ),
            )
            resolved_profile_id = resolve_channel_update_profile_id(
                profile_id=profile_id,
                current_profile_id=current.profile_id,
            )
            load_channel_profile(settings=settings, profile_id=resolved_profile_id)
            resolved_trigger_mode = _resolve_trigger_mode(
                value=None if interactive else trigger_mode,
                interactive=interactive,
                lang=prompt_language,
                default=current.trigger_mode,
            )
            resolved_tool_profile = (
                normalize_channel_tool_profile(
                    resolve_channel_choice(
                        value=None,
                        interactive=True,
                        prompt_en="Channel tool profile",
                        prompt_ru="Профиль инструментов канала",
                        default=current.tool_profile,
                        allowed=CHANNEL_TOOL_PROFILE_VALUES,
                        lang=prompt_language,
                    )
                )
                if interactive
                else normalize_channel_tool_profile(tool_profile or current.tool_profile)
            )
            saved = _update_partyflow_endpoint(
                settings=settings,
                endpoint=PartyFlowPollingEndpointConfig(
                    endpoint_id=current.endpoint_id,
                    profile_id=resolved_profile_id,
                    credential_profile_key=resolve_channel_text(
                        value=credential_profile_key,
                        interactive=False,
                        prompt_en="Credential profile",
                        prompt_ru="Credential profile",
                        default=current.credential_profile_key,
                        lang=prompt_language,
                        normalize_lower=True,
                    ),
                    account_id=resolve_channel_text(
                        value=account_id,
                        interactive=False,
                        prompt_en="Account id",
                        prompt_ru="Account id",
                        default=current.account_id,
                        lang=prompt_language,
                        normalize_lower=True,
                    ),
                    enabled=current.enabled,
                    trigger_mode=resolved_trigger_mode,  # type: ignore[arg-type]
                    trigger_keywords=_resolve_trigger_keywords(
                        interactive=interactive,
                        lang=prompt_language,
                        trigger_mode=resolved_trigger_mode,
                        trigger_keywords=trigger_keywords,
                        current_trigger_mode=current.trigger_mode,
                        current_trigger_keywords=current.trigger_keywords,
                    ),
                    reply_mode=resolve_channel_choice(
                        value=None if interactive else reply_mode,
                        interactive=interactive,
                        prompt_en="PartyFlow reply mode",
                        prompt_ru="Режим ответа PartyFlow",
                        default=current.reply_mode,
                        allowed=_PARTYFLOW_REPLY_MODES,
                        lang=prompt_language,
                    ),  # type: ignore[arg-type]
                    tool_profile=resolved_tool_profile,
                    access_policy=collect_channel_access_policy_inputs(
                        interactive=interactive,
                        lang=prompt_language,
                        private_policy=private_policy,
                        allow_from=allow_from,
                        group_policy=group_policy,
                        groups=groups,
                        group_allow_from=group_allow_from,
                        outbound_allow_to=outbound_allow_to,
                        tool_profile=resolved_tool_profile,
                        private_policy_default=current.access_policy.private_policy,
                        allow_from_default=current.access_policy.allow_from,
                        group_policy_default=current.access_policy.group_policy,
                        groups_default=current.access_policy.groups,
                        group_allow_from_default=current.access_policy.group_allow_from,
                        outbound_allow_to_default=current.access_policy.outbound_allow_to,
                    ),
                    ingress_batch=merge_ingress_batch_config(
                        current=current.ingress_batch,
                        enabled=(
                            current.ingress_batch.enabled
                            if ingress_batch_enabled is None
                            else ingress_batch_enabled
                        ),
                        debounce_ms=ingress_debounce_ms,
                        cooldown_sec=ingress_cooldown_sec,
                        max_batch_size=ingress_max_batch_size,
                        max_buffer_chars=ingress_max_buffer_chars,
                    ),
                ),
            )
            if sync_binding:
                binding_inputs = resolve_binding_update_inputs(
                    settings=settings,
                    binding_id=saved.endpoint_id,
                    session_policy=session_policy,
                    session_policy_default="per-thread",
                    priority=priority,
                    prompt_overlay=prompt_overlay,
                )
                binding_count = put_access_policy_bindings(
                    settings=settings,
                    endpoint_id=saved.endpoint_id,
                    transport=saved.transport,
                    profile_id=saved.profile_id,
                    session_policy=binding_inputs.session_policy,
                    priority=binding_inputs.priority,
                    enabled=saved.enabled,
                    account_id=saved.account_id,
                    prompt_overlay=binding_inputs.prompt_overlay,
                    access_policy=saved.access_policy,
                    replace_existing=True,
                )
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        if json_output:
            typer.echo(json.dumps({"channel": saved.model_dump(mode="json")}, ensure_ascii=True))
            reload_install_managed_runtime_notice(settings)
            return
        typer.echo(
            f"PartyFlow polling channel `{saved.endpoint_id}` updated for profile `{saved.profile_id}` "
            f"(credential_profile={saved.credential_profile_key}, account_id={saved.account_id}, "
            f"trigger_mode={saved.trigger_mode}, reply_mode={saved.reply_mode}, "
            f"tool_profile={saved.tool_profile}, ingress_batch={saved.ingress_batch.enabled}, "
            f"enabled={saved.enabled})."
        )
        if saved.trigger_keywords:
            typer.echo(f"- trigger_keywords: {', '.join(saved.trigger_keywords)}")
        _render_access_policy(saved)
        if sync_binding:
            typer.echo(f"Matching bindings updated: {binding_count}.")
        reload_install_managed_runtime_notice(settings)

    @partyflow_app.command("list")
    def partyflow_list(
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        try:
            channels = run_channel_endpoint_service_sync(
                settings,
                lambda service: service.list(transport="partyflow"),
            )
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        typed = [
            PartyFlowPollingEndpointConfig.model_validate(item.model_dump())
            for item in channels
            if item.adapter_kind == "partyflow_polling"
        ]
        unsupported = [
            _unsupported_partyflow_row(item)
            for item in channels
            if item.adapter_kind != "partyflow_polling"
        ]
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "channels": [item.model_dump(mode="json") for item in typed],
                        "unsupported_partyflow": unsupported,
                    },
                    ensure_ascii=True,
                )
            )
            return
        if not typed and not unsupported:
            typer.echo("No PartyFlow channels configured.")
            return
        for item in typed:
            typer.echo(
                f"- {item.endpoint_id}: profile={item.profile_id}, "
                f"credential_profile={item.credential_profile_key}, account_id={item.account_id}, "
                f"trigger_mode={item.trigger_mode}, trigger_keywords={','.join(item.trigger_keywords) or '-'}, "
                f"reply_mode={item.reply_mode}, access={_render_access_policy_summary(item)}, "
                f"ingress_batch={render_ingress_batch_summary(item.ingress_batch)}, enabled={item.enabled}"
            )
        for unsupported_item in unsupported:
            typer.echo(
                f"- {unsupported_item['endpoint_id']}: "
                f"unsupported adapter_kind={unsupported_item['adapter_kind']}, "
                f"enabled={unsupported_item['enabled']}"
            )
            typer.echo(f"  reason: {unsupported_item['reason']}")

    @partyflow_app.command("show")
    def partyflow_show(
        channel_id: str = typer.Argument(..., help="PartyFlow channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        try:
            channel = _load_partyflow_endpoint(settings=settings, channel_id=channel_id)
            profile = _load_profile(settings=settings, profile_id=channel.profile_id)
            inspection = build_channel_inspection_summary(
                settings=settings,
                profile=profile,
                channel=channel,
            )
            state_path = partyflow_polling_state_path_for(settings, endpoint_id=channel.endpoint_id)
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        payload = {
            "channel": channel.model_dump(mode="json"),
            "state_path": str(state_path),
            "state_present": state_path.exists(),
            "mutation_state": inspection.mutation_state.model_dump(mode="json"),
            "profile_ceiling": inspection.profile_ceiling.model_dump(mode="json"),
            "effective_permissions": inspection.effective_permissions.model_dump(mode="json"),
        }
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"PartyFlow channel `{channel.endpoint_id}`")
        typer.echo(f"- profile: {channel.profile_id}")
        typer.echo(f"- credential_profile: {channel.credential_profile_key}")
        typer.echo(f"- account_id: {channel.account_id}")
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
        typer.echo("- delivery_mode: poll")
        typer.echo(f"- trigger_mode: {channel.trigger_mode}")
        typer.echo("- trigger_keywords: " + (", ".join(channel.trigger_keywords) or "-"))
        typer.echo(f"- reply_mode: {channel.reply_mode}")
        typer.echo(f"- tool_profile: {channel.tool_profile}")
        _render_access_policy(channel)
        typer.echo(f"- ingress_batch.enabled: {channel.ingress_batch.enabled}")
        typer.echo(f"- ingress_batch.debounce_ms: {channel.ingress_batch.debounce_ms}")
        typer.echo(f"- ingress_batch.cooldown_sec: {channel.ingress_batch.cooldown_sec}")
        typer.echo(f"- ingress_batch.max_batch_size: {channel.ingress_batch.max_batch_size}")
        typer.echo(f"- ingress_batch.max_buffer_chars: {channel.ingress_batch.max_buffer_chars}")
        typer.echo(f"- polling_state_path: {state_path}")
        typer.echo(f"- polling_state_present: {state_path.exists()}")
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
        typer.echo(f"- enabled: {channel.enabled}")

    @partyflow_app.command("status")
    def partyflow_status(
        channel_id: str | None = typer.Argument(None),
        probe: bool = typer.Option(False, "--probe", help="Run live PartyFlow get_me probe."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        try:
            payload = asyncio.run(
                _partyflow_status_payload(settings=settings, channel_id=channel_id, probe=probe)
            )
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
        else:
            _render_partyflow_status_payload(payload)
        if payload.get("ok") is False:
            raise typer.Exit(code=1)

    @partyflow_app.command("poll-once")
    def partyflow_poll_once(
        channel_id: str = typer.Argument(...),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        try:
            payload = asyncio.run(_partyflow_poll_once_payload(settings=settings, channel_id=channel_id))
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(
            f"PartyFlow poll-once `{channel_id}` processed_events={payload['processed_events']} "
            f"state_path={payload['state_path']}"
        )

    @partyflow_app.command("reset-cursor")
    def partyflow_reset_cursor(
        channel_id: str = typer.Argument(...),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        try:
            payload = asyncio.run(_partyflow_reset_cursor_payload(settings=settings, channel_id=channel_id))
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        if json_output:
            typer.echo(json.dumps(payload, ensure_ascii=True))
            return
        typer.echo(f"PartyFlow polling cursor reset for `{channel_id}` removed={payload['removed']}.")

    @partyflow_app.command("enable")
    def partyflow_enable(channel_id: str = typer.Argument(...)) -> None:
        _set_partyflow_enabled(channel_id=channel_id, enabled=True)

    @partyflow_app.command("disable")
    def partyflow_disable(channel_id: str = typer.Argument(...)) -> None:
        _set_partyflow_enabled(channel_id=channel_id, enabled=False)

    @partyflow_app.command("delete")
    def partyflow_delete(
        channel_id: str = typer.Argument(...),
        keep_binding: bool = typer.Option(False, "--keep-binding"),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        try:
            _load_partyflow_endpoint(settings=settings, channel_id=channel_id)
            run_channel_endpoint_service_sync(
                settings,
                lambda service: service.delete(endpoint_id=channel_id),
            )
            binding_removed = False
            if not keep_binding:
                binding_removed = _delete_partyflow_bindings(
                    settings=settings,
                    channel_id=channel_id,
                )
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        if json_output:
            typer.echo(json.dumps({"ok": True, "binding_removed": binding_removed}, ensure_ascii=True))
            reload_install_managed_runtime_notice(settings)
            return
        typer.echo(f"PartyFlow channel `{channel_id}` deleted.")
        if binding_removed:
            typer.echo(f"Matching binding `{channel_id}` deleted.")
        reload_install_managed_runtime_notice(settings)


def _resolve_trigger_mode(
    *,
    value: str | None,
    interactive: bool,
    lang: PromptLanguage,
    default: str,
) -> str:
    return resolve_channel_choice(
        value=value,
        interactive=interactive,
        prompt_en="PartyFlow trigger mode",
        prompt_ru="Режим триггера PartyFlow",
        default=default,
        allowed=_PARTYFLOW_TRIGGER_MODES,
        lang=lang,
        detail_en=(
            "Choose which delivered MESSAGE_CREATED events may start an agent turn: "
            "all messages, only bot mentions, or keyword matches."
        ),
        detail_ru=(
            "Выберите, какие доставленные события MESSAGE_CREATED могут запускать ход агента: "
            "все сообщения, только упоминания бота или ключевые слова."
        ),
    )


def _resolve_trigger_keywords(
    *,
    interactive: bool,
    lang: PromptLanguage,
    trigger_mode: str,
    trigger_keywords: str | None,
    current_trigger_mode: str,
    current_trigger_keywords: tuple[str, ...],
) -> tuple[str, ...]:
    if trigger_mode != "keywords":
        return ()
    default_keywords = ", ".join(current_trigger_keywords) if current_trigger_mode == "keywords" else None
    raw_keywords = resolve_channel_text(
        value=None if interactive else trigger_keywords,
        interactive=interactive,
        prompt_en="PartyFlow trigger keywords",
        prompt_ru="Ключевые слова-триггеры PartyFlow",
        default=default_keywords,
        lang=lang,
        detail_en="Comma-separated keywords that should trigger AFKBOT in keywords mode.",
        detail_ru="Ключевые слова через запятую, по которым AFKBOT должен срабатывать.",
    )
    return _split_csv_patterns(raw_keywords)


def _split_csv_patterns(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    seen: set[str] = set()
    normalized: list[str] = []
    for part in raw.split(","):
        keyword = part.strip().lower()
        if not keyword or keyword in seen:
            continue
        seen.add(keyword)
        normalized.append(keyword)
    return tuple(normalized)


def _render_access_policy_summary(endpoint: PartyFlowPollingEndpointConfig) -> str:
    policy = endpoint.access_policy
    outbound = "restricted" if policy.outbound_allow_to else "open"
    return f"private={policy.private_policy},group={policy.group_policy},outbound={outbound}"


def _render_access_policy(endpoint: PartyFlowPollingEndpointConfig) -> None:
    typer.echo(f"- access.private_policy: {endpoint.access_policy.private_policy}")
    typer.echo("- access.allow_from: " + (", ".join(endpoint.access_policy.allow_from) or "-"))
    typer.echo(f"- access.group_policy: {endpoint.access_policy.group_policy}")
    typer.echo("- access.groups: " + (", ".join(endpoint.access_policy.groups) or "-"))
    typer.echo(
        "- access.group_allow_from: "
        + (", ".join(endpoint.access_policy.group_allow_from) or "-")
    )
    typer.echo(
        "- access.outbound_allow_to: "
        + (", ".join(endpoint.access_policy.outbound_allow_to) or "-")
    )


def _load_partyflow_endpoint(*, settings: Settings, channel_id: str) -> PartyFlowPollingEndpointConfig:
    endpoint = run_channel_endpoint_service_sync(
        settings,
        lambda service: service.get(endpoint_id=channel_id),
    )
    return _coerce_partyflow_endpoint(endpoint=endpoint, channel_id=channel_id)


async def _load_partyflow_endpoint_async(
    *,
    settings: Settings,
    channel_id: str,
) -> PartyFlowPollingEndpointConfig:
    endpoint = await get_channel_endpoint_service(settings).get(endpoint_id=channel_id)
    return _coerce_partyflow_endpoint(endpoint=endpoint, channel_id=channel_id)


def _coerce_partyflow_endpoint(
    *,
    endpoint: ChannelEndpointConfig,
    channel_id: str,
) -> PartyFlowPollingEndpointConfig:
    if endpoint.transport != "partyflow" or endpoint.adapter_kind != "partyflow_polling":
        raise ChannelEndpointServiceError(
            error_code="channel_endpoint_type_mismatch",
            reason=f"Channel endpoint `{channel_id}` is not a PartyFlow polling channel.",
        )
    return PartyFlowPollingEndpointConfig.model_validate(endpoint.model_dump())


def _load_profile(*, settings: Settings, profile_id: str) -> ProfileDetails:
    return run_profile_service_sync(settings, lambda service: service.get(profile_id=profile_id))


def _create_partyflow_endpoint(
    *,
    settings: Settings,
    endpoint: PartyFlowPollingEndpointConfig,
) -> PartyFlowPollingEndpointConfig:
    created = run_channel_endpoint_service_sync(settings, lambda service: service.create(endpoint))
    return PartyFlowPollingEndpointConfig.model_validate(created.model_dump())


def _update_partyflow_endpoint(
    *,
    settings: Settings,
    endpoint: PartyFlowPollingEndpointConfig,
) -> PartyFlowPollingEndpointConfig:
    updated = run_channel_endpoint_service_sync(settings, lambda service: service.update(endpoint))
    return PartyFlowPollingEndpointConfig.model_validate(updated.model_dump())


def _set_partyflow_enabled(*, channel_id: str, enabled: bool) -> None:
    settings = get_settings()
    try:
        current = _load_partyflow_endpoint(settings=settings, channel_id=channel_id)
        updated = run_channel_endpoint_service_sync(
            settings,
            lambda service: service.update(current.model_copy(update={"enabled": enabled})),
        )
        run_channel_binding_service_sync(
            settings,
            lambda service: _sync_partyflow_binding_enabled(
                service=service,
                channel_id=channel_id,
                enabled=enabled,
            ),
        )
    except Exception as exc:
        _raise_partyflow_cli_error(exc)
    typer.echo(f"PartyFlow channel `{updated.endpoint_id}` enabled={updated.enabled}.")
    reload_install_managed_runtime_notice(settings)


async def _sync_partyflow_binding_enabled(
    *,
    service: ChannelBindingService,
    channel_id: str,
    enabled: bool,
) -> None:
    bindings = await service.list(transport="partyflow")
    for binding in bindings:
        if binding.binding_id != channel_id and not binding.binding_id.startswith(f"{channel_id}:"):
            continue
        await service.put(
            ChannelBindingRule(**(binding.model_dump(mode="python") | {"enabled": enabled}))
        )


def _delete_partyflow_bindings(*, settings: Settings, channel_id: str) -> bool:
    async def _delete_rules(service: ChannelBindingService) -> bool:
        rules = await service.list(transport="partyflow")
        removed = False
        for rule in rules:
            if rule.binding_id != channel_id and not rule.binding_id.startswith(f"{channel_id}:"):
                continue
            try:
                await service.delete(binding_id=rule.binding_id)
                removed = True
            except ChannelBindingServiceError as exc:
                if exc.error_code != "channel_binding_not_found":
                    raise
        return removed

    return run_channel_binding_service_sync(settings, _delete_rules)


async def _partyflow_status_payload(
    *,
    settings: Settings,
    channel_id: str | None,
    probe: bool,
) -> dict[str, object]:
    endpoint_service = get_channel_endpoint_service(settings)
    binding_service = get_channel_binding_service(settings)
    if channel_id is not None:
        endpoint = await endpoint_service.get(endpoint_id=channel_id)
        if endpoint.transport != "partyflow":
            raise ChannelEndpointServiceError(
                error_code="channel_endpoint_type_mismatch",
                reason=f"Channel endpoint `{channel_id}` is not a PartyFlow channel.",
            )
        if endpoint.adapter_kind != "partyflow_polling":
            unsupported = [_unsupported_partyflow_row(endpoint)]
            return {"ok": False, "partyflow_polling": [], "unsupported_partyflow": unsupported}
        endpoints = [PartyFlowPollingEndpointConfig.model_validate(endpoint.model_dump())]
        unsupported = []
    else:
        listed = await endpoint_service.list(transport="partyflow")
        endpoints = [
            PartyFlowPollingEndpointConfig.model_validate(item.model_dump())
            for item in listed
            if item.adapter_kind == "partyflow_polling"
        ]
        unsupported = [
            _unsupported_partyflow_row(item)
            for item in listed
            if item.adapter_kind != "partyflow_polling"
        ]
    bindings = await binding_service.list(transport="partyflow")
    rows = [
        await _partyflow_status_row(
            settings=settings,
            endpoint=endpoint,
            binding_count=sum(
                1
                for binding in bindings
                if binding.binding_id == endpoint.endpoint_id
                or binding.binding_id.startswith(f"{endpoint.endpoint_id}:")
            ),
            probe=probe,
        )
        for endpoint in endpoints
    ]
    return {
        "ok": not unsupported and all(row.get("ok") is not False for row in rows),
        "partyflow_polling": rows,
        "unsupported_partyflow": unsupported,
    }


def _unsupported_partyflow_row(endpoint: ChannelEndpointConfig) -> dict[str, object]:
    return {
        "endpoint_id": endpoint.endpoint_id,
        "adapter_kind": endpoint.adapter_kind,
        "enabled": endpoint.enabled,
        "profile_id": endpoint.profile_id,
        "credential_profile_key": endpoint.credential_profile_key,
        "account_id": endpoint.account_id,
        "reason": UNSUPPORTED_PARTYFLOW_WEBHOOK_REASON,
    }


async def _partyflow_status_row(
    *,
    settings: Settings,
    endpoint: PartyFlowPollingEndpointConfig,
    binding_count: int,
    probe: bool,
) -> dict[str, object]:
    token_status = await _partyflow_credential_status(
        settings=settings,
        endpoint=endpoint,
        credential_name=_PARTYFLOW_BOT_TOKEN,
    )
    state_path = partyflow_polling_state_path_for(settings, endpoint_id=endpoint.endpoint_id)
    row: dict[str, object] = {
        "ok": True,
        "endpoint_id": endpoint.endpoint_id,
        "enabled": endpoint.enabled,
        "profile_id": endpoint.profile_id,
        "credential_profile_key": endpoint.credential_profile_key,
        "account_id": endpoint.account_id,
        "delivery_mode": "poll",
        "trigger_mode": endpoint.trigger_mode,
        "reply_mode": endpoint.reply_mode,
        "bot_token_configured": token_status["configured"],
        "binding_count": binding_count,
        "state_path": str(state_path),
        "state_present": await asyncio.to_thread(state_path.exists),
    }
    if token_status["configured"] is False:
        row["ok"] = False
        row["bot_token_error"] = token_status["reason"]
    if probe:
        probe_payload = await _probe_partyflow_endpoint(settings=settings, endpoint=endpoint)
        row["probe"] = probe_payload
        if probe_payload.get("ok") is False:
            row["ok"] = False
    return row


async def _partyflow_credential_status(
    *,
    settings: Settings,
    endpoint: PartyFlowPollingEndpointConfig,
    credential_name: str,
) -> dict[str, object]:
    try:
        await get_credentials_service(settings).resolve_metadata_for_app_tool(
            profile_id=endpoint.profile_id,
            tool_name="app.run",
            integration_name="partyflow",
            credential_profile_key=endpoint.credential_profile_key,
            credential_name=credential_name,
        )
    except CredentialsServiceError as exc:
        return {"configured": False, "error_code": exc.error_code, "reason": exc.reason}
    return {"configured": True}


async def _probe_partyflow_endpoint(
    *,
    settings: Settings,
    endpoint: PartyFlowPollingEndpointConfig,
) -> dict[str, object]:
    try:
        token = await get_credentials_service(settings).resolve_plaintext_for_app_tool(
            profile_id=endpoint.profile_id,
            tool_name="app.run",
            integration_name="partyflow",
            credential_profile_key=endpoint.credential_profile_key,
            credential_name=_PARTYFLOW_BOT_TOKEN,
        )
        result = await _get_me(
            base_url=PARTYFLOW_API_BASE_URL,
            token=token,
            timeout_sec=min(10, settings.tool_timeout_max_sec),
        )
    except CredentialsServiceError as exc:
        return {
            "ok": False,
            "error_code": exc.error_code,
            "reason": exc.reason,
            "metadata": exc.details,
        }
    except PartyFlowApiError as exc:
        return {
            "ok": False,
            "error_code": exc.error_code,
            "reason": exc.reason,
            "metadata": exc.metadata,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_code": "partyflow_probe_failed",
            "reason": f"{exc.__class__.__name__}: {exc}",
            "metadata": {},
        }
    bot = result.get("bot")
    payload: dict[str, object] = {"ok": True}
    if isinstance(bot, dict):
        payload["bot_id"] = str(bot.get("id") or "")
        payload["display_name"] = str(bot.get("display_name") or bot.get("name") or "")
        payload["is_active"] = bool(bot.get("is_active", True))
        if payload["is_active"] is False:
            payload["ok"] = False
            payload["error_code"] = "partyflow_bot_inactive"
            payload["reason"] = "PartyFlow bot is inactive."
    return payload


def _render_partyflow_status_payload(payload: dict[str, object]) -> None:
    endpoints = payload.get("partyflow_polling")
    unsupported = payload.get("unsupported_partyflow")
    if (
        (not isinstance(endpoints, list) or not endpoints)
        and (not isinstance(unsupported, list) or not unsupported)
    ):
        typer.echo("No PartyFlow channels configured.")
        return
    endpoint_rows = endpoints if isinstance(endpoints, list) else []
    typer.echo(f"PartyFlow polling endpoints: {len(endpoint_rows)}")
    for item in endpoint_rows:
        if not isinstance(item, dict):
            continue
        typer.echo(
            f"- {item['endpoint_id']}: enabled={item['enabled']}, profile={item['profile_id']}, "
            f"credential_profile={item['credential_profile_key']}, account_id={item['account_id']}, "
            f"bot_token_configured={item['bot_token_configured']}, "
            f"binding_count={item['binding_count']}, state_present={item['state_present']}"
        )
        typer.echo(f"  state_path: {item['state_path']}")
        probe = item.get("probe")
        if isinstance(probe, dict):
            if probe.get("ok") is True:
                typer.echo(
                    f"  probe: ok bot_id={probe.get('bot_id', '')} "
                    f"display_name={probe.get('display_name', '')}"
                )
            else:
                typer.echo(f"  probe: ERROR [{probe.get('error_code')}] {probe.get('reason')}")
    unsupported_rows = unsupported if isinstance(unsupported, list) else []
    if unsupported_rows:
        typer.echo(f"Unsupported PartyFlow endpoints: {len(unsupported_rows)}")
    for item in unsupported_rows:
        if not isinstance(item, dict):
            continue
        typer.echo(
            f"- {item['endpoint_id']}: adapter_kind={item['adapter_kind']}, "
            f"enabled={item['enabled']}"
        )
        typer.echo(f"  reason: {item['reason']}")


async def _partyflow_poll_once_payload(
    *,
    settings: Settings,
    channel_id: str,
) -> dict[str, object]:
    endpoint = await _load_partyflow_endpoint_async(settings=settings, channel_id=channel_id)
    state_path = partyflow_polling_state_path_for(settings, endpoint_id=endpoint.endpoint_id)
    service = PartyFlowPollingService(settings, endpoint=endpoint, state_path=state_path)
    processed = await service.poll_once()
    return {
        "ok": True,
        "channel_id": channel_id,
        "processed_events": processed,
        "state_path": str(state_path),
    }


async def _partyflow_reset_cursor_payload(
    *,
    settings: Settings,
    channel_id: str,
) -> dict[str, object]:
    endpoint = await _load_partyflow_endpoint_async(settings=settings, channel_id=channel_id)
    state_path = partyflow_polling_state_path_for(settings, endpoint_id=endpoint.endpoint_id)
    service = PartyFlowPollingService(settings, endpoint=endpoint, state_path=state_path)
    removed = await service.reset_saved_cursor()
    return {"ok": True, "channel_id": channel_id, "removed": removed, "state_path": str(state_path)}


def _raise_partyflow_cli_error(exc: Exception) -> None:
    if isinstance(exc, (ChannelEndpointServiceError, ChannelBindingServiceError)):
        raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
    raise_usage_error(str(exc))
