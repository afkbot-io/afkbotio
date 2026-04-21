"""PartyFlow webhook channel CLI registration and operations."""

from __future__ import annotations

import ipaddress
import json
from typing import cast
from urllib.parse import urlparse

import typer

from afkbot.cli.command_errors import raise_usage_error
from afkbot.cli.commands.channel_credentials_support import configure_partyflow_channel_credentials
from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_int,
    resolve_channel_text,
)
from afkbot.cli.commands.channel_shared import (
    build_generated_channel_id,
    build_ingress_batch_config,
    collect_channel_add_base_inputs,
    load_channel_profile,
    merge_ingress_batch_config,
    normalize_channel_tool_profile,
    put_matching_binding,
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
from afkbot.services.channel_routing import ChannelBindingRule
from afkbot.services.channel_routing.contracts import SessionPolicy
from afkbot.services.channel_routing.service import (
    ChannelBindingServiceError,
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
    PartyFlowWebhookEndpointConfig,
)
from afkbot.services.channels.endpoint_service import (
    ChannelEndpointServiceError,
    run_channel_endpoint_service_sync,
)
from afkbot.services.channels.tool_profiles import (
    CHANNEL_TOOL_PROFILE_HELP,
    CHANNEL_TOOL_PROFILE_VALUES,
)
from afkbot.services.profile_runtime import ProfileDetails, run_profile_service_sync
from afkbot.settings import Settings, get_settings

_PARTYFLOW_INGRESS_MODES = ("webhook",)
_PARTYFLOW_TRIGGER_MODES = ("all", "mention", "keywords")
_PARTYFLOW_REPLY_MODES = ("same_conversation", "disabled")


def register_partyflow_commands(channel_app: typer.Typer) -> None:
    """Register PartyFlow channel controls under `afk channel partyflow`."""

    partyflow_app = typer.Typer(
        help="PartyFlow outgoing-webhook controls.",
        no_args_is_help=True,
    )
    channel_app.add_typer(partyflow_app, name="partyflow")

    @partyflow_app.command("add")
    def partyflow_add(
        channel_id: str | None = typer.Argument(
            None,
            help="Stable channel id used later in show/delete commands. Omit it to let the wizard suggest an auto-generated id.",
        ),
        profile_id: str | None = typer.Option(None, "--profile", help="Target profile-agent id."),
        credential_profile_key: str | None = typer.Option(
            None,
            "--credential-profile",
            help="PartyFlow credential profile key holding the bot token and webhook signing secret. Defaults to the channel id.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Logical account id used by bindings and routing telemetry. Defaults to the channel id.",
        ),
        enabled: bool | None = typer.Option(
            None, "--enabled/--disabled", help="Enable or disable this endpoint."
        ),
        ingress_mode: str | None = typer.Option(
            None,
            "--ingress-mode",
            help="Ingress mode. For now only `webhook` is supported.",
        ),
        trigger_mode: str | None = typer.Option(
            None,
            "--trigger-mode",
            help="Webhook trigger mode: all, mention, keywords.",
        ),
        trigger_keywords: str | None = typer.Option(
            None,
            "--trigger-keywords",
            help="Comma-separated lowercase-insensitive keywords used when --trigger-mode keywords is selected.",
        ),
        include_context: bool | None = typer.Option(
            None,
            "--include-context/--no-include-context",
            help="Expect PartyFlow to include recent conversation context in webhook payloads.",
        ),
        context_size: int | None = typer.Option(
            None,
            "--context-size",
            help="Requested PartyFlow context_size for webhook subscription setup.",
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
            help="Delay and coalesce sequential inbound webhook messages before one turn.",
        ),
        ingress_debounce_ms: int | None = typer.Option(
            None,
            "--ingress-debounce-ms",
            help="Quiet-window delay before flushing one coalesced inbound batch.",
        ),
        ingress_cooldown_sec: int | None = typer.Option(
            None,
            "--ingress-cooldown-sec",
            help="Minimum seconds between processed inbound batches per conversation when ingress batching is enabled.",
        ),
        ingress_max_batch_size: int | None = typer.Option(
            None,
            "--ingress-max-batch-size",
            help="Maximum inbound messages merged into one turn before immediate flush.",
        ),
        ingress_max_buffer_chars: int | None = typer.Option(
            None,
            "--ingress-max-buffer-chars",
            help="Maximum buffered text chars per coalesced inbound batch.",
        ),
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
        priority: int = typer.Option(
            0, "--priority", help="Binding priority when --binding is enabled."
        ),
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Use defaults plus explicit flags without interactive questions. Missing channel id is auto-generated.",
        ),
        lang: str | None = typer.Option(None, "--lang", help="Interactive language: en or ru."),
        ru: bool = typer.Option(False, "--ru", help="Shortcut for --lang ru in interactive mode."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        interactive = should_collect_channel_add_interactively(
            yes=yes,
            channel_id=channel_id,
            profile_id=profile_id,
            credential_profile_key=credential_profile_key,
        )
        prompt_language = resolve_prompt_language(settings=settings, value=lang, ru=ru)
        generated_channel_id = build_generated_channel_id(transport="partyflow")
        try:
            if interactive:
                render_channel_add_intro(
                    transport="partyflow",
                    lang=prompt_language,
                    suggested_channel_id=generated_channel_id,
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
                tool_profile=tool_profile,
                create_binding=create_binding,
                session_policy=session_policy,
                binding_session_policy_default="per-thread",
                binding_session_policy_allowed=(
                    "main",
                    "per-chat",
                    "per-thread",
                    "per-user-in-group",
                ),
                generated_channel_id=generated_channel_id,
            )
            resolved_ingress_mode = resolve_channel_choice(
                value=ingress_mode,
                interactive=interactive,
                prompt_en="PartyFlow ingress mode",
                prompt_ru="Режим входящих событий PartyFlow",
                default="webhook",
                allowed=_PARTYFLOW_INGRESS_MODES,
                lang=prompt_language,
                detail_en="PartyFlow polling is not available here yet; webhook is the only supported mode.",
                detail_ru="Polling для PartyFlow здесь пока недоступен; сейчас поддерживается только webhook.",
            )
            resolved_trigger_mode = resolve_channel_choice(
                value=trigger_mode,
                interactive=interactive,
                prompt_en="PartyFlow trigger mode",
                prompt_ru="Режим триггера PartyFlow",
                default="mention",
                allowed=_PARTYFLOW_TRIGGER_MODES,
                lang=prompt_language,
                detail_en="This mirrors the PartyFlow webhook subscription trigger: reply to all messages, only mentions, or keyword matches.",
                detail_ru="Это повторяет режим срабатывания подписки webhook в PartyFlow: отвечать на все сообщения, только на упоминания или по ключевым словам.",
            )
            resolved_trigger_keywords = (
                _split_csv_patterns(
                    resolve_channel_text(
                        value=trigger_keywords,
                        interactive=interactive,
                        prompt_en="PartyFlow trigger keywords",
                        prompt_ru="Ключевые слова-триггеры PartyFlow",
                        default=None,
                        lang=prompt_language,
                        detail_en="Comma-separated keywords that should trigger AFKBOT when PartyFlow trigger mode is `keywords`.",
                        detail_ru="Ключевые слова через запятую, по которым AFKBOT должен срабатывать в режиме `keywords`. Каждое слово должно быть длиной от 2 до 100 символов.",
                    )
                )
                if resolved_trigger_mode == "keywords"
                else ()
            )
            resolved_include_context = resolve_channel_bool(
                value=include_context,
                interactive=interactive,
                prompt_en="Include PartyFlow context?",
                prompt_ru="Подтягивать контекст из PartyFlow?",
                default=True,
                lang=prompt_language,
                detail_en="When enabled, the webhook payload should include recent channel messages so AFKBOT sees short local history without extra read API calls.",
                detail_ru="Если включено, payload webhook должен содержать недавние сообщения канала, чтобы AFKBOT видел короткую локальную историю без дополнительных вызовов API чтения.",
            )
            resolved_context_size = resolve_channel_int(
                value=context_size,
                interactive=interactive and resolved_include_context,
                prompt_en="PartyFlow context size",
                prompt_ru="Размер контекста PartyFlow",
                default=8,
                lang=prompt_language,
                min_value=1,
                max_value=50,
                detail_en="How many recent PartyFlow messages should be attached to each webhook delivery.",
                detail_ru="Сколько последних сообщений PartyFlow нужно прикладывать к каждой доставке webhook.",
            )
            resolved_reply_mode = resolve_channel_choice(
                value=reply_mode,
                interactive=interactive,
                prompt_en="PartyFlow reply mode",
                prompt_ru="Режим ответа PartyFlow",
                default="same_conversation",
                allowed=_PARTYFLOW_REPLY_MODES,
                lang=prompt_language,
                detail_en="`same_conversation` replies back into the same PartyFlow channel/thread; `disabled` keeps the channel ingress-only.",
                detail_ru="`same_conversation` отправляет ответ обратно в тот же канал или тред PartyFlow; `disabled` оставляет канал только входящим.",
            )
            resolved_ingress_enabled = resolve_channel_bool(
                value=ingress_batch_enabled,
                interactive=interactive,
                prompt_en="Enable ingress batching?",
                prompt_ru="Включить пакетирование входящих webhook-сообщений?",
                default=False,
                lang=prompt_language,
                detail_en="Batching waits for a short quiet window and merges bursts of inbound webhook messages into one turn.",
                detail_ru="Пакетирование ждёт короткое окно тишины и объединяет серию входящих webhook-сообщений в один ход агента.",
            )
            resolved_ingress_batch = build_ingress_batch_config(
                enabled=resolved_ingress_enabled,
                debounce_ms=resolve_channel_int(
                    value=ingress_debounce_ms,
                    interactive=interactive and resolved_ingress_enabled,
                    prompt_en="Ingress debounce (ms)",
                    prompt_ru="Задержка пакетирования (мс)",
                    default=1500,
                    lang=prompt_language,
                    min_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
                    max_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
                ),
                cooldown_sec=resolve_channel_int(
                    value=ingress_cooldown_sec,
                    interactive=interactive and resolved_ingress_enabled,
                    prompt_en="Ingress cooldown (sec)",
                    prompt_ru="Пауза между пакетами (сек)",
                    default=0,
                    lang=prompt_language,
                    min_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
                    max_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
                ),
                max_batch_size=resolve_channel_int(
                    value=ingress_max_batch_size,
                    interactive=interactive and resolved_ingress_enabled,
                    prompt_en="Ingress max batch size",
                    prompt_ru="Максимальный размер входящего пакета",
                    default=20,
                    lang=prompt_language,
                    min_value=CHANNEL_INGRESS_BATCH_SIZE_MIN,
                    max_value=CHANNEL_INGRESS_BATCH_SIZE_MAX,
                ),
                max_buffer_chars=resolve_channel_int(
                    value=ingress_max_buffer_chars,
                    interactive=interactive and resolved_ingress_enabled,
                    prompt_en="Ingress max buffer chars",
                    prompt_ru="Максимальный размер буфера в символах",
                    default=12000,
                    lang=prompt_language,
                    min_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
                    max_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
                ),
            )
            endpoint = PartyFlowWebhookEndpointConfig(
                endpoint_id=base_inputs.channel_id,
                profile_id=base_inputs.profile_id,
                credential_profile_key=base_inputs.credential_profile_key,
                account_id=base_inputs.account_id,
                enabled=base_inputs.enabled,
                ingress_mode=resolved_ingress_mode,  # type: ignore[arg-type]
                trigger_mode=resolved_trigger_mode,  # type: ignore[arg-type]
                trigger_keywords=resolved_trigger_keywords,
                include_context=resolved_include_context,
                context_size=resolved_context_size,
                reply_mode=resolved_reply_mode,  # type: ignore[arg-type]
                tool_profile=base_inputs.tool_profile,
                ingress_batch=resolved_ingress_batch,
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
            if base_inputs.create_binding:
                put_matching_binding(
                    settings=settings,
                    binding_id=saved.endpoint_id,
                    transport=saved.transport,
                    profile_id=saved.profile_id,
                    session_policy=base_inputs.session_policy,
                    priority=priority,
                    enabled=saved.enabled,
                    account_id=saved.account_id,
                    prompt_overlay=prompt_overlay,
                )
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        if json_output:
            typer.echo(json.dumps({"channel": saved.model_dump(mode="json")}, ensure_ascii=True))
            reload_install_managed_runtime_notice(settings)
            return
        webhook_url = resolve_partyflow_webhook_url(
            settings=settings, endpoint_id=saved.endpoint_id
        )
        typer.echo(
            msg(
                prompt_language,
                en=(
                    "PartyFlow channel created "
                    f"(id={saved.endpoint_id}, trigger_mode={saved.trigger_mode}, "
                    f"ingress_batch={saved.ingress_batch.enabled}, reply_mode={saved.reply_mode})."
                ),
                ru=(
                    "Канал PartyFlow создан "
                    f"(id={saved.endpoint_id}, trigger_mode={saved.trigger_mode}, "
                    f"ingress_batch={saved.ingress_batch.enabled}, reply_mode={saved.reply_mode})."
                ),
            )
        )
        if saved.trigger_keywords:
            typer.echo(f"- trigger_keywords: {', '.join(saved.trigger_keywords)}")
        typer.echo(f"- webhook_url: {webhook_url or 'unavailable'}")
        typer.echo(
            msg(
                prompt_language,
                en="- configure PartyFlow subscription event_types: MESSAGE_CREATED, MESSAGE_UPDATED",
                ru="- настройте в подписке PartyFlow event_types: MESSAGE_CREATED, MESSAGE_UPDATED",
            )
        )
        typer.echo(
            msg(
                prompt_language,
                en=f"- configure PartyFlow subscription trigger: {saved.trigger_mode}",
                ru=f"- настройте в подписке PartyFlow режим срабатывания: {saved.trigger_mode}",
            )
        )
        typer.echo(
            msg(
                prompt_language,
                en=(
                    "- configure PartyFlow subscription include_context/context_size: "
                    f"{saved.include_context}/{saved.context_size}"
                ),
                ru=(
                    "- настройте в подписке PartyFlow include_context/context_size: "
                    f"{saved.include_context}/{saved.context_size}"
                ),
            )
        )
        if webhook_url is None:
            typer.echo(
                _render_partyflow_webhook_url_unavailable_hint(
                    prompt_language,
                    reason=_resolve_partyflow_webhook_url(
                        settings=settings,
                        endpoint_id=saved.endpoint_id,
                    )[1],
                )
            )
        reload_install_managed_runtime_notice(settings)

    @partyflow_app.command("update")
    def partyflow_update(
        channel_id: str = typer.Argument(..., help="Stable PartyFlow channel endpoint id."),
        profile_id: str | None = typer.Option(None, "--profile", help="Target profile-agent id."),
        credential_profile_key: str | None = typer.Option(
            None,
            "--credential-profile",
            help="PartyFlow credential profile key holding the bot token and webhook signing secret.",
        ),
        account_id: str | None = typer.Option(
            None,
            "--account-id",
            help="Channel account id used by bindings and routing telemetry.",
        ),
        trigger_mode: str | None = typer.Option(
            None,
            "--trigger-mode",
            help="Webhook trigger mode: all, mention, keywords.",
        ),
        trigger_keywords: str | None = typer.Option(
            None,
            "--trigger-keywords",
            help="Comma-separated keywords used when --trigger-mode keywords is selected.",
        ),
        include_context: bool | None = typer.Option(
            None,
            "--include-context/--no-include-context",
            help="Expect PartyFlow to include recent conversation context in webhook payloads.",
        ),
        context_size: int | None = typer.Option(
            None,
            "--context-size",
            help="Requested PartyFlow context_size for webhook subscription setup.",
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
            help="Delay and coalesce sequential inbound webhook messages before one turn.",
        ),
        ingress_debounce_ms: int | None = typer.Option(
            None,
            "--ingress-debounce-ms",
            help="Quiet-window delay before flushing one coalesced inbound batch.",
        ),
        ingress_cooldown_sec: int | None = typer.Option(
            None,
            "--ingress-cooldown-sec",
            help="Minimum seconds between processed inbound batches per conversation when ingress batching is enabled.",
        ),
        ingress_max_batch_size: int | None = typer.Option(
            None,
            "--ingress-max-batch-size",
            help="Maximum inbound messages merged into one turn before immediate flush.",
        ),
        ingress_max_buffer_chars: int | None = typer.Option(
            None,
            "--ingress-max-buffer-chars",
            help="Maximum buffered text chars per coalesced inbound batch.",
        ),
        yes: bool = typer.Option(
            False,
            "--yes",
            help="Use current values plus explicit flags without interactive questions.",
        ),
        lang: str | None = typer.Option(None, "--lang", help="Interactive language: en or ru."),
        ru: bool = typer.Option(False, "--ru", help="Shortcut for --lang ru in interactive mode."),
        sync_binding: bool = typer.Option(
            False,
            "--binding",
            help="Also update the matching routing binding to the current profile/account values.",
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
        priority: int | None = typer.Option(
            None, "--priority", help="Binding priority when --binding is enabled."
        ),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
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
                    include_context,
                    context_size,
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
            resolved_trigger_mode = resolve_channel_choice(
                value=None if interactive else trigger_mode,
                interactive=interactive,
                prompt_en="PartyFlow trigger mode",
                prompt_ru="Режим триггера PartyFlow",
                default=current.trigger_mode,
                allowed=_PARTYFLOW_TRIGGER_MODES,
                lang=prompt_language,
                detail_en="This mirrors the PartyFlow webhook subscription trigger: reply to all messages, only mentions, or keyword matches.",
                detail_ru="Это повторяет режим срабатывания подписки webhook в PartyFlow: отвечать на все сообщения, только на упоминания или по ключевым словам.",
            )
            resolved_trigger_keywords = _resolve_trigger_keywords(
                interactive=interactive,
                lang=prompt_language,
                trigger_mode=resolved_trigger_mode,
                trigger_keywords=trigger_keywords,
                current_trigger_mode=current.trigger_mode,
                current_trigger_keywords=current.trigger_keywords,
            )
            resolved_include_context = (
                resolve_channel_bool(
                    value=None,
                    interactive=True,
                    prompt_en="Include PartyFlow context?",
                    prompt_ru="Подтягивать контекст из PartyFlow?",
                    default=current.include_context,
                    lang=prompt_language,
                    detail_en="When enabled, the webhook payload should include recent channel messages so AFKBOT sees short local history without extra read API calls.",
                    detail_ru="Если включено, payload webhook должен содержать недавние сообщения канала, чтобы AFKBOT видел короткую локальную историю без дополнительных вызовов API чтения.",
                )
                if interactive
                else (current.include_context if include_context is None else include_context)
            )
            resolved_context_size = (
                resolve_channel_int(
                    value=None if interactive else context_size,
                    interactive=interactive and resolved_include_context,
                    prompt_en="PartyFlow context size",
                    prompt_ru="Размер контекста PartyFlow",
                    default=current.context_size,
                    lang=prompt_language,
                    min_value=1,
                    max_value=50,
                    detail_en="How many recent PartyFlow messages should be attached to each webhook delivery.",
                    detail_ru="Сколько последних сообщений PartyFlow нужно прикладывать к каждой доставке webhook.",
                )
                if resolved_include_context
                else current.context_size
            )
            resolved_reply_mode = resolve_channel_choice(
                value=None if interactive else reply_mode,
                interactive=interactive,
                prompt_en="PartyFlow reply mode",
                prompt_ru="Режим ответа PartyFlow",
                default=current.reply_mode,
                allowed=_PARTYFLOW_REPLY_MODES,
                lang=prompt_language,
                detail_en="`same_conversation` replies back into the same PartyFlow channel/thread; `disabled` keeps the channel ingress-only.",
                detail_ru="`same_conversation` отправляет ответ обратно в тот же канал или тред PartyFlow; `disabled` оставляет канал только входящим.",
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
            resolved_ingress_enabled = (
                resolve_channel_bool(
                    value=None,
                    interactive=True,
                    prompt_en="Enable ingress batching?",
                    prompt_ru="Включить пакетирование входящих webhook-сообщений?",
                    default=current.ingress_batch.enabled,
                    lang=prompt_language,
                    detail_en="Batching waits for a short quiet window and merges bursts of inbound webhook messages into one turn.",
                    detail_ru="Пакетирование ждёт короткое окно тишины и объединяет серию входящих webhook-сообщений в один ход агента.",
                )
                if interactive
                else (
                    current.ingress_batch.enabled
                    if ingress_batch_enabled is None
                    else ingress_batch_enabled
                )
            )
            resolved_ingress_batch = merge_ingress_batch_config(
                current=current.ingress_batch,
                enabled=resolved_ingress_enabled,
                debounce_ms=(
                    resolve_channel_int(
                        value=None if interactive else ingress_debounce_ms,
                        interactive=interactive and resolved_ingress_enabled,
                        prompt_en="Ingress debounce (ms)",
                        prompt_ru="Задержка пакетирования (мс)",
                        default=current.ingress_batch.debounce_ms,
                        lang=prompt_language,
                        min_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
                        max_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
                    )
                    if resolved_ingress_enabled
                    else None
                ),
                cooldown_sec=(
                    resolve_channel_int(
                        value=None if interactive else ingress_cooldown_sec,
                        interactive=interactive and resolved_ingress_enabled,
                        prompt_en="Ingress cooldown (sec)",
                        prompt_ru="Пауза между пакетами (сек)",
                        default=current.ingress_batch.cooldown_sec,
                        lang=prompt_language,
                        min_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
                        max_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
                    )
                    if resolved_ingress_enabled
                    else None
                ),
                max_batch_size=(
                    resolve_channel_int(
                        value=None if interactive else ingress_max_batch_size,
                        interactive=interactive and resolved_ingress_enabled,
                        prompt_en="Ingress max batch size",
                        prompt_ru="Максимальный размер входящего пакета",
                        default=current.ingress_batch.max_batch_size,
                        lang=prompt_language,
                        min_value=CHANNEL_INGRESS_BATCH_SIZE_MIN,
                        max_value=CHANNEL_INGRESS_BATCH_SIZE_MAX,
                    )
                    if resolved_ingress_enabled
                    else None
                ),
                max_buffer_chars=(
                    resolve_channel_int(
                        value=None if interactive else ingress_max_buffer_chars,
                        interactive=interactive and resolved_ingress_enabled,
                        prompt_en="Ingress max buffer chars",
                        prompt_ru="Максимальный размер буфера в символах",
                        default=current.ingress_batch.max_buffer_chars,
                        lang=prompt_language,
                        min_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
                        max_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
                    )
                    if resolved_ingress_enabled
                    else None
                ),
            )
            saved = _update_partyflow_endpoint(
                settings=settings,
                endpoint=PartyFlowWebhookEndpointConfig(
                    endpoint_id=current.endpoint_id,
                    profile_id=resolved_profile_id,
                    credential_profile_key=resolve_channel_text(
                        value=credential_profile_key,
                        interactive=False,
                        prompt_en="Credential profile",
                        prompt_ru="Credential profile",
                        default=current.credential_profile_key or current.endpoint_id,
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
                    ingress_mode=current.ingress_mode,
                    trigger_mode=resolved_trigger_mode,  # type: ignore[arg-type]
                    trigger_keywords=resolved_trigger_keywords,
                    include_context=resolved_include_context,
                    context_size=resolved_context_size,
                    reply_mode=resolved_reply_mode,  # type: ignore[arg-type]
                    tool_profile=resolved_tool_profile,
                    ingress_batch=resolved_ingress_batch,
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
                put_matching_binding(
                    settings=settings,
                    binding_id=saved.endpoint_id,
                    transport=saved.transport,
                    profile_id=saved.profile_id,
                    session_policy=binding_inputs.session_policy,
                    priority=binding_inputs.priority,
                    enabled=saved.enabled,
                    account_id=saved.account_id,
                    prompt_overlay=binding_inputs.prompt_overlay,
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
                    f"PartyFlow channel `{saved.endpoint_id}` updated for profile `{saved.profile_id}` "
                    f"(credential_profile={saved.credential_profile_key}, account_id={saved.account_id}, "
                    f"trigger_mode={saved.trigger_mode}, reply_mode={saved.reply_mode}, "
                    f"tool_profile={saved.tool_profile}, ingress_batch={saved.ingress_batch.enabled}, enabled={saved.enabled})."
                ),
                ru=(
                    f"Канал PartyFlow `{saved.endpoint_id}` обновлён для профиля `{saved.profile_id}` "
                    f"(credential_profile={saved.credential_profile_key}, account_id={saved.account_id}, "
                    f"trigger_mode={saved.trigger_mode}, reply_mode={saved.reply_mode}, "
                    f"tool_profile={saved.tool_profile}, ingress_batch={saved.ingress_batch.enabled}, enabled={saved.enabled})."
                ),
            )
        )
        if saved.trigger_keywords:
            typer.echo(f"- trigger_keywords: {', '.join(saved.trigger_keywords)}")
        if sync_binding:
            typer.echo(
                msg(
                    prompt_language,
                    en=f"Matching binding `{saved.endpoint_id}` was also updated.",
                    ru=f"Связанная привязка `{saved.endpoint_id}` тоже обновлена.",
                )
            )
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
            PartyFlowWebhookEndpointConfig.model_validate(item.model_dump()) for item in channels
        ]
        if json_output:
            typer.echo(
                json.dumps(
                    {"channels": [item.model_dump(mode="json") for item in typed]},
                    ensure_ascii=True,
                )
            )
            return
        if not typed:
            typer.echo("No PartyFlow channels configured.")
            return
        for item in typed:
            typer.echo(
                f"- {item.endpoint_id}: profile={item.profile_id}, credential_profile={item.credential_profile_key}, "
                f"account_id={item.account_id}, ingress_mode={item.ingress_mode}, trigger_mode={item.trigger_mode}, "
                f"trigger_keywords={','.join(item.trigger_keywords) or '-'}, "
                f"reply_mode={item.reply_mode}, ingress_batch={render_ingress_batch_summary(item.ingress_batch)}, enabled={item.enabled}"
            )

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
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        webhook_url = resolve_partyflow_webhook_url(
            settings=settings, endpoint_id=channel.endpoint_id
        )
        payload = {
            "channel": channel.model_dump(mode="json"),
            "webhook_url": webhook_url,
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
        typer.echo(f"- ingress_mode: {channel.ingress_mode}")
        typer.echo(f"- trigger_mode: {channel.trigger_mode}")
        typer.echo("- trigger_keywords: " + (", ".join(channel.trigger_keywords) or "-"))
        typer.echo(f"- include_context: {channel.include_context}")
        typer.echo(f"- context_size: {channel.context_size}")
        typer.echo(f"- reply_mode: {channel.reply_mode}")
        typer.echo(f"- tool_profile: {channel.tool_profile}")
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
        typer.echo(f"- enabled: {channel.enabled}")
        typer.echo(f"- webhook_url: {webhook_url or 'unavailable'}")
        if webhook_url is None:
            typer.echo(
                _render_partyflow_webhook_url_unavailable_hint(
                    None,
                    reason=_resolve_partyflow_webhook_url(
                        settings=settings,
                        endpoint_id=channel.endpoint_id,
                    )[1],
                )
            )

    @partyflow_app.command("webhook-url")
    def partyflow_webhook_url(
        channel_id: str = typer.Argument(..., help="PartyFlow channel endpoint id."),
        json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of human text."),
    ) -> None:
        settings = get_settings()
        try:
            channel = _load_partyflow_endpoint(settings=settings, channel_id=channel_id)
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        webhook_url, reason = _resolve_partyflow_webhook_url(
            settings=settings,
            endpoint_id=channel.endpoint_id,
        )
        if json_output:
            typer.echo(
                json.dumps(
                    {
                        "channel_id": channel.endpoint_id,
                        "webhook_url": webhook_url,
                        "status": "ok" if webhook_url is not None else "unavailable",
                        "reason": reason,
                    },
                    ensure_ascii=True,
                )
            )
            return
        if webhook_url is None:
            raise_usage_error(_partyflow_webhook_url_unavailable_message(reason))
        typer.echo(webhook_url)

    @partyflow_app.command("enable")
    def partyflow_enable(
        channel_id: str = typer.Argument(..., help="PartyFlow channel endpoint id."),
    ) -> None:
        _set_partyflow_enabled(channel_id=channel_id, enabled=True)

    @partyflow_app.command("disable")
    def partyflow_disable(
        channel_id: str = typer.Argument(..., help="PartyFlow channel endpoint id."),
    ) -> None:
        _set_partyflow_enabled(channel_id=channel_id, enabled=False)

    @partyflow_app.command("delete")
    def partyflow_delete(
        channel_id: str = typer.Argument(..., help="PartyFlow channel endpoint id."),
        keep_binding: bool = typer.Option(
            False, "--keep-binding", help="Keep the matching routing binding."
        ),
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
                try:
                    run_channel_binding_service_sync(
                        settings,
                        lambda service: service.delete(binding_id=channel_id),
                    )
                    binding_removed = True
                except ChannelBindingServiceError:
                    binding_removed = False
        except Exception as exc:
            _raise_partyflow_cli_error(exc)
        if json_output:
            typer.echo(
                json.dumps({"ok": True, "binding_removed": binding_removed}, ensure_ascii=True)
            )
            reload_install_managed_runtime_notice(settings)
            return
        typer.echo(f"PartyFlow channel `{channel_id}` deleted.")
        if binding_removed:
            typer.echo(f"Matching binding `{channel_id}` deleted.")
        reload_install_managed_runtime_notice(settings)


def resolve_partyflow_webhook_url(*, settings: Settings, endpoint_id: str) -> str | None:
    """Return best-effort PartyFlow webhook URL for one endpoint."""

    return _resolve_partyflow_webhook_url(settings=settings, endpoint_id=endpoint_id)[0]


def _resolve_partyflow_webhook_url(
    *, settings: Settings, endpoint_id: str
) -> tuple[str | None, str | None]:
    public_chat_api_url = (settings.public_chat_api_url or "").strip()
    if not public_chat_api_url:
        return None, "missing_public_base_url"
    parsed = urlparse(public_chat_api_url)
    if parsed.scheme.lower() != "https":
        return None, "non_https_public_base_url"
    hostname = (parsed.hostname or "").strip()
    if not hostname:
        return None, "invalid_public_base_url"
    lowered_hostname = hostname.lower()
    if lowered_hostname in {"localhost", "127.0.0.1", "::1"}:
        return None, "private_public_base_url"
    try:
        parsed_ip = ipaddress.ip_address(lowered_hostname)
    except ValueError:
        parsed_ip = None
    if parsed_ip is not None and (
        parsed_ip.is_private
        or parsed_ip.is_loopback
        or parsed_ip.is_link_local
        or parsed_ip.is_multicast
        or parsed_ip.is_reserved
        or parsed_ip.is_unspecified
    ):
        return None, "private_public_base_url"
    base_url = public_chat_api_url.rstrip("/")
    return f"{base_url}/v1/channels/partyflow/{endpoint_id}/webhook", None


def _partyflow_webhook_url_unavailable_message(reason: str | None) -> str:
    if reason == "non_https_public_base_url":
        return (
            "Webhook URL unavailable: `AFKBOT_PUBLIC_CHAT_API_URL` must use public HTTPS "
            "(PartyFlow rejects plain HTTP)."
        )
    if reason == "private_public_base_url":
        return (
            "Webhook URL unavailable: `AFKBOT_PUBLIC_CHAT_API_URL` points to localhost/private "
            "addressing, but PartyFlow requires a public HTTPS endpoint."
        )
    if reason == "invalid_public_base_url":
        return (
            "Webhook URL unavailable: `AFKBOT_PUBLIC_CHAT_API_URL` is invalid. Set it to a "
            "public HTTPS base URL."
        )
    return (
        "Webhook URL unavailable: set `AFKBOT_PUBLIC_CHAT_API_URL` to a public HTTPS base URL "
        "first."
    )


def _render_partyflow_webhook_url_unavailable_hint(
    lang: PromptLanguage | None, *, reason: str | None
) -> str:
    resolved_lang = cast(PromptLanguage, lang or "en")
    if reason == "non_https_public_base_url":
        return msg(
            resolved_lang,
            en=(
                "- webhook_url is unavailable because `AFKBOT_PUBLIC_CHAT_API_URL` must use "
                "public HTTPS; PartyFlow rejects plain HTTP."
            ),
            ru=(
                "- webhook_url недоступен, потому что `AFKBOT_PUBLIC_CHAT_API_URL` должен "
                "использовать публичный HTTPS; PartyFlow не принимает plain HTTP."
            ),
        )
    if reason == "private_public_base_url":
        return msg(
            resolved_lang,
            en=(
                "- webhook_url is unavailable because `AFKBOT_PUBLIC_CHAT_API_URL` points to "
                "localhost/private addressing, but PartyFlow requires a public HTTPS endpoint."
            ),
            ru=(
                "- webhook_url недоступен, потому что `AFKBOT_PUBLIC_CHAT_API_URL` указывает "
                "на localhost/private адрес, а PartyFlow требует публичный HTTPS endpoint."
            ),
        )
    if reason == "invalid_public_base_url":
        return msg(
            resolved_lang,
            en=(
                "- webhook_url is unavailable because `AFKBOT_PUBLIC_CHAT_API_URL` is invalid. "
                "Set it to a public HTTPS base URL."
            ),
            ru=(
                "- webhook_url недоступен, потому что `AFKBOT_PUBLIC_CHAT_API_URL` задан "
                "некорректно. Укажите публичный HTTPS base URL."
            ),
        )
    return msg(
        resolved_lang,
        en=(
            "- webhook_url is unavailable until `AFKBOT_PUBLIC_CHAT_API_URL` points to a public "
            "HTTPS base URL."
        ),
        ru=(
            "- webhook_url недоступен, пока `AFKBOT_PUBLIC_CHAT_API_URL` не указывает на "
            "публичный HTTPS base URL."
        ),
    )


def _load_partyflow_endpoint(
    *, settings: Settings, channel_id: str
) -> PartyFlowWebhookEndpointConfig:
    endpoint = run_channel_endpoint_service_sync(
        settings,
        lambda service: service.get(endpoint_id=channel_id),
    )
    if endpoint.transport != "partyflow" or endpoint.adapter_kind != "partyflow_webhook":
        raise ChannelEndpointServiceError(
            error_code="channel_endpoint_type_mismatch",
            reason=f"Channel endpoint `{channel_id}` is not a PartyFlow webhook channel.",
        )
    return PartyFlowWebhookEndpointConfig.model_validate(endpoint.model_dump())


def _load_profile(*, settings: Settings, profile_id: str) -> ProfileDetails:
    return run_profile_service_sync(settings, lambda service: service.get(profile_id=profile_id))


def _create_partyflow_endpoint(
    *,
    settings: Settings,
    endpoint: PartyFlowWebhookEndpointConfig,
) -> PartyFlowWebhookEndpointConfig:
    created = run_channel_endpoint_service_sync(
        settings,
        lambda service: service.create(endpoint),
    )
    return PartyFlowWebhookEndpointConfig.model_validate(created.model_dump())


def _update_partyflow_endpoint(
    *,
    settings: Settings,
    endpoint: PartyFlowWebhookEndpointConfig,
) -> PartyFlowWebhookEndpointConfig:
    updated = run_channel_endpoint_service_sync(
        settings,
        lambda service: service.update(endpoint),
    )
    return PartyFlowWebhookEndpointConfig.model_validate(updated.model_dump())


def _set_partyflow_enabled(*, channel_id: str, enabled: bool) -> None:
    settings = get_settings()
    try:
        current = _load_partyflow_endpoint(settings=settings, channel_id=channel_id)
        updated = run_channel_endpoint_service_sync(
            settings,
            lambda service: service.update(current.model_copy(update={"enabled": enabled})),
        )
        try:
            binding = run_channel_binding_service_sync(
                settings,
                lambda service: service.get(binding_id=channel_id),
            )
            run_channel_binding_service_sync(
                settings,
                lambda service: service.put(
                    ChannelBindingRule(**(binding.model_dump(mode="python") | {"enabled": enabled}))
                ),
            )
        except ChannelBindingServiceError:
            pass
    except Exception as exc:
        _raise_partyflow_cli_error(exc)
    typer.echo(f"PartyFlow channel `{updated.endpoint_id}` enabled={updated.enabled}.")
    reload_install_managed_runtime_notice(settings)


def _raise_partyflow_cli_error(exc: Exception) -> None:
    if isinstance(exc, (ChannelEndpointServiceError, ChannelBindingServiceError)):
        raise_usage_error(f"ERROR [{exc.error_code}] {exc.reason}")
    raise_usage_error(str(exc))


def _split_csv_patterns(raw: str | None) -> tuple[str, ...]:
    """Split one CLI CSV string into stable normalized PartyFlow keywords."""

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


def _render_trigger_keywords_csv(values: tuple[str, ...]) -> str:
    return ", ".join(values)


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
    default_keywords = (
        _render_trigger_keywords_csv(current_trigger_keywords)
        if current_trigger_mode == "keywords"
        else None
    )
    raw_keywords = resolve_channel_text(
        value=None if interactive else trigger_keywords,
        interactive=interactive,
        prompt_en="PartyFlow trigger keywords",
        prompt_ru="Ключевые слова-триггеры PartyFlow",
        default=default_keywords,
        lang=lang,
        detail_en="Comma-separated keywords that should trigger AFKBOT when PartyFlow trigger mode is `keywords`.",
        detail_ru="Ключевые слова через запятую, по которым AFKBOT должен срабатывать в режиме `keywords`. Каждое слово должно быть длиной от 2 до 100 символов.",
    )
    return _split_csv_patterns(raw_keywords)
