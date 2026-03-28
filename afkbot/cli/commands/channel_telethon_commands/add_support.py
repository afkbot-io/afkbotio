"""Create-flow support for Telethon channel CLI commands."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from afkbot.cli.commands.channel_credentials_support import (
    configure_telethon_channel_credentials,
)
from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_int,
    resolve_channel_text,
)
from afkbot.cli.commands.channel_shared import (
    build_ingress_batch_config,
    build_reply_humanization_config,
    collect_channel_add_base_inputs,
    put_matching_binding,
    should_collect_channel_add_interactively,
)
from afkbot.cli.commands.channel_telethon_commands.common import (
    TELETHON_GROUP_INVOCATION_MODES,
    TELETHON_REPLY_MODES,
    normalize_telethon_group_invocation_mode,
    normalize_telethon_reply_mode,
    split_csv_patterns,
)
from afkbot.cli.commands.channel_telethon_commands.legacy import (
    get_legacy_channel_endpoint_service,
)
from afkbot.cli.commands.channel_telethon_commands.watcher import build_watcher_config
from afkbot.cli.presentation.setup_prompts import normalize_prompt_language
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
    TELETHON_WATCHER_BATCH_INTERVAL_SEC_MAX,
    TELETHON_WATCHER_BATCH_INTERVAL_SEC_MIN,
    TELETHON_WATCHER_BATCH_SIZE_MAX,
    TELETHON_WATCHER_BATCH_SIZE_MIN,
    TELETHON_WATCHER_BUFFER_SIZE_MAX,
    TELETHON_WATCHER_BUFFER_SIZE_MIN,
    TELETHON_WATCHER_MESSAGE_CHARS_MAX,
    TELETHON_WATCHER_MESSAGE_CHARS_MIN,
    TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MAX,
    TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MIN,
    TelethonUserEndpointConfig,
)
from afkbot.services.channels.telethon_user.runtime_support import evaluate_telethon_profile_policy
from afkbot.settings import get_settings


@dataclass(frozen=True, slots=True)
class TelethonCreateResult:
    """Result of one Telethon channel create flow."""

    saved: TelethonUserEndpointConfig
    binding_created: bool
    binding_warning: str | None
    policy_warning: str | None


def create_telethon_channel(
    *,
    channel_id: str | None,
    profile_id: str | None,
    credential_profile_key: str | None,
    account_id: str | None,
    enabled: bool | None,
    reply_mode: str | None,
    tool_profile: str | None,
    reply_blocked_chat_patterns: str | None,
    reply_allowed_chat_patterns: str | None,
    group_invocation_mode: str | None,
    process_self_commands: bool | None,
    command_prefix: str | None,
    ingress_batch_enabled: bool | None,
    ingress_debounce_ms: int | None,
    ingress_cooldown_sec: int | None,
    ingress_max_batch_size: int | None,
    ingress_max_buffer_chars: int | None,
    humanize_replies: bool | None,
    humanize_min_delay_ms: int | None,
    humanize_max_delay_ms: int | None,
    humanize_chars_per_second: int | None,
    mark_read_before_reply: bool | None,
    watcher_enabled: bool | None,
    watcher_unmuted_only: bool | None,
    watcher_include_private: bool | None,
    watcher_include_groups: bool | None,
    watcher_include_channels: bool | None,
    watcher_batch_interval_sec: int | None,
    watcher_dialog_refresh_interval_sec: int | None,
    watcher_max_batch_size: int | None,
    watcher_max_buffer_size: int | None,
    watcher_max_message_chars: int | None,
    watcher_blocked_chat_patterns: str | None,
    watcher_allowed_chat_patterns: str | None,
    watcher_delivery_transport: str | None,
    watcher_delivery_account_id: str | None,
    watcher_delivery_peer_id: str | None,
    watcher_delivery_credential_profile_key: str | None,
    create_binding: bool | None,
    session_policy: SessionPolicy | None,
    prompt_overlay: str | None,
    priority: int,
    yes: bool,
    lang: str | None,
    ru: bool,
) -> TelethonCreateResult:
    """Create one Telethon endpoint using the current CLI mutation rules."""

    settings = get_settings()
    interactive = should_collect_channel_add_interactively(
        yes=yes,
        channel_id=channel_id,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
    )
    prompt_language = normalize_prompt_language(value=lang, ru=ru)
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
        binding_session_policy_default="per-chat",
        binding_session_policy_allowed=("main", "per-chat", "per-thread", "per-user-in-group"),
    )
    resolved_reply_mode = normalize_telethon_reply_mode(
        resolve_channel_choice(
            value=reply_mode,
            interactive=interactive,
            prompt_en="Telethon reply mode",
            prompt_ru="Режим ответов Telethon",
            default="disabled",
            allowed=TELETHON_REPLY_MODES,
            lang=prompt_language,
            detail_en="Choose whether the user account should stay read-only in this channel or send replies back to the same chat.",
            detail_ru="Выберите, должен ли user-аккаунт только читать этот канал или ещё и отправлять ответы обратно в тот же чат.",
        )
    )
    resolved_group_invocation_mode = normalize_telethon_group_invocation_mode(
        resolve_channel_choice(
            value=group_invocation_mode,
            interactive=interactive,
            prompt_en="Telethon group invocation mode",
            prompt_ru="Режим вызова Telethon в группах",
            default="reply_or_command",
            allowed=TELETHON_GROUP_INVOCATION_MODES,
            lang=prompt_language,
            detail_en="This controls when group messages are allowed to trigger the agent: on replies, commands, mentions, or all messages.",
            detail_ru="Это определяет, когда сообщения в группах могут запускать агента: по reply, по командам, по mention или на все сообщения.",
        )
    )
    resolved_process_self_commands = resolve_channel_bool(
        value=process_self_commands,
        interactive=interactive,
        prompt_en="Process self commands?",
        prompt_ru="Обрабатывать собственные команды?",
        default=False,
        lang=prompt_language,
        detail_en="Enable this only if you want to control the userbot channel with your own messages such as `.afk status`.",
        detail_ru="Включайте это только если хотите управлять userbot-каналом своими сообщениями вроде `.afk status`.",
    )
    resolved_command_prefix = resolve_channel_text(
        value=command_prefix,
        interactive=interactive and resolved_process_self_commands,
        prompt_en="Command prefix",
        prompt_ru="Префикс команды",
        default=".afk",
        lang=prompt_language,
    )
    resolved_ingress_enabled = resolve_channel_bool(
        value=ingress_batch_enabled,
        interactive=interactive,
        prompt_en="Enable ingress batching?",
        prompt_ru="Включить batching входящих сообщений?",
        default=False,
        lang=prompt_language,
        detail_en="Batching waits a short quiet window and merges bursts of inbound messages into one turn, which helps against flood and spam.",
        detail_ru="Batching ждёт короткое окно тишины и объединяет всплески входящих сообщений в один turn, что помогает против флуда и спама.",
    )
    resolved_ingress_batch = build_ingress_batch_config(
        enabled=resolved_ingress_enabled,
        debounce_ms=resolve_channel_int(
            value=ingress_debounce_ms,
            interactive=interactive and resolved_ingress_enabled,
            prompt_en="Ingress debounce (ms)",
            prompt_ru="Ingress debounce (мс)",
            default=1500,
            lang=prompt_language,
            min_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
            max_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
        ),
        cooldown_sec=resolve_channel_int(
            value=ingress_cooldown_sec,
            interactive=interactive and resolved_ingress_enabled,
            prompt_en="Ingress cooldown (sec)",
            prompt_ru="Ingress cooldown (сек)",
            default=0,
            lang=prompt_language,
            min_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
            max_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
        ),
        max_batch_size=resolve_channel_int(
            value=ingress_max_batch_size,
            interactive=interactive and resolved_ingress_enabled,
            prompt_en="Ingress max batch size",
            prompt_ru="Максимальный размер ingress batch",
            default=20,
            lang=prompt_language,
            min_value=CHANNEL_INGRESS_BATCH_SIZE_MIN,
            max_value=CHANNEL_INGRESS_BATCH_SIZE_MAX,
        ),
        max_buffer_chars=resolve_channel_int(
            value=ingress_max_buffer_chars,
            interactive=interactive and resolved_ingress_enabled,
            prompt_en="Ingress max buffer chars",
            prompt_ru="Максимальный размер ingress buffer в символах",
            default=12000,
            lang=prompt_language,
            min_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
            max_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
        ),
    )
    resolved_humanize_replies = resolve_channel_bool(
        value=humanize_replies,
        interactive=interactive,
        prompt_en="Enable humanized replies?",
        prompt_ru="Включить humanized replies?",
        default=False,
        lang=prompt_language,
    )
    resolved_reply_humanization = build_reply_humanization_config(
        enabled=resolved_humanize_replies,
        min_delay_ms=resolve_channel_int(
            value=humanize_min_delay_ms,
            interactive=interactive and resolved_humanize_replies,
            prompt_en="Humanized min delay (ms)",
            prompt_ru="Минимальная задержка humanized replies (мс)",
            default=1000,
            lang=prompt_language,
            min_value=0,
        ),
        max_delay_ms=resolve_channel_int(
            value=humanize_max_delay_ms,
            interactive=interactive and resolved_humanize_replies,
            prompt_en="Humanized max delay (ms)",
            prompt_ru="Максимальная задержка humanized replies (мс)",
            default=8000,
            lang=prompt_language,
            min_value=0,
        ),
        chars_per_second=resolve_channel_int(
            value=humanize_chars_per_second,
            interactive=interactive and resolved_humanize_replies,
            prompt_en="Typing speed (chars/sec)",
            prompt_ru="Скорость печати (символов/сек)",
            default=12,
            lang=prompt_language,
            min_value=1,
        ),
    )
    resolved_mark_read_before_reply = resolve_channel_bool(
        value=mark_read_before_reply,
        interactive=interactive,
        prompt_en="Mark chat as read before reply?",
        prompt_ru="Отмечать чат прочитанным перед ответом?",
        default=True,
        lang=prompt_language,
    )
    resolved_watcher_enabled = resolve_channel_bool(
        value=watcher_enabled,
        interactive=interactive,
        prompt_en="Enable watcher mode?",
        prompt_ru="Включить watcher mode?",
        default=False,
        lang=prompt_language,
    )
    resolved_watcher = build_watcher_config(
        enabled=resolved_watcher_enabled,
        unmuted_only=resolve_channel_bool(
            value=watcher_unmuted_only,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Watch only unmuted dialogs?",
            prompt_ru="Следить только за unmuted диалогами?",
            default=True,
            lang=prompt_language,
        ),
        include_private=resolve_channel_bool(
            value=watcher_include_private,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Include private chats in watcher?",
            prompt_ru="Включать личные чаты в watcher?",
            default=True,
            lang=prompt_language,
        ),
        include_groups=resolve_channel_bool(
            value=watcher_include_groups,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Include groups in watcher?",
            prompt_ru="Включать группы в watcher?",
            default=True,
            lang=prompt_language,
        ),
        include_channels=resolve_channel_bool(
            value=watcher_include_channels,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Include channels in watcher?",
            prompt_ru="Включать каналы в watcher?",
            default=True,
            lang=prompt_language,
        ),
        batch_interval_sec=resolve_channel_int(
            value=watcher_batch_interval_sec,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Watcher batch interval (sec)",
            prompt_ru="Интервал watcher batch (сек)",
            default=300,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_BATCH_INTERVAL_SEC_MIN,
            max_value=TELETHON_WATCHER_BATCH_INTERVAL_SEC_MAX,
        ),
        dialog_refresh_interval_sec=resolve_channel_int(
            value=watcher_dialog_refresh_interval_sec,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Watcher dialog refresh interval (sec)",
            prompt_ru="Интервал обновления диалогов watcher (сек)",
            default=300,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MIN,
            max_value=TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MAX,
        ),
        max_batch_size=resolve_channel_int(
            value=watcher_max_batch_size,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Watcher max batch size",
            prompt_ru="Максимальный размер watcher batch",
            default=100,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_BATCH_SIZE_MIN,
            max_value=TELETHON_WATCHER_BATCH_SIZE_MAX,
        ),
        max_buffer_size=resolve_channel_int(
            value=watcher_max_buffer_size,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Watcher max buffer size",
            prompt_ru="Максимальный размер watcher buffer",
            default=500,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_BUFFER_SIZE_MIN,
            max_value=TELETHON_WATCHER_BUFFER_SIZE_MAX,
        ),
        max_message_chars=resolve_channel_int(
            value=watcher_max_message_chars,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Watcher max message chars",
            prompt_ru="Максимальная длина сообщения watcher",
            default=1000,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_MESSAGE_CHARS_MIN,
            max_value=TELETHON_WATCHER_MESSAGE_CHARS_MAX,
        ),
        blocked_chat_patterns=split_csv_patterns(watcher_blocked_chat_patterns),
        allowed_chat_patterns=split_csv_patterns(watcher_allowed_chat_patterns),
        delivery_transport=watcher_delivery_transport,
        delivery_account_id=watcher_delivery_account_id,
        delivery_peer_id=watcher_delivery_peer_id,
        delivery_credential_profile_key=watcher_delivery_credential_profile_key,
    )
    endpoint = TelethonUserEndpointConfig(
        endpoint_id=base_inputs.channel_id,
        profile_id=base_inputs.profile_id,
        credential_profile_key=base_inputs.credential_profile_key,
        account_id=base_inputs.account_id,
        enabled=base_inputs.enabled,
        reply_mode=resolved_reply_mode,
        tool_profile=base_inputs.tool_profile,
        reply_blocked_chat_patterns=split_csv_patterns(reply_blocked_chat_patterns),
        reply_allowed_chat_patterns=split_csv_patterns(reply_allowed_chat_patterns),
        group_invocation_mode=resolved_group_invocation_mode,
        process_self_commands=resolved_process_self_commands,
        command_prefix=resolved_command_prefix,
        ingress_batch=resolved_ingress_batch,
        reply_humanization=resolved_reply_humanization,
        mark_read_before_reply=resolved_mark_read_before_reply,
        watcher=resolved_watcher,
    )
    if interactive and credential_profile_key is None:
        configure_telethon_channel_credentials(
            settings=settings,
            profile_id=base_inputs.profile_id,
            credential_profile_key=base_inputs.credential_profile_key,
            interactive=True,
            lang=prompt_language,
        )
    saved = TelethonUserEndpointConfig.model_validate(
        asyncio.run(get_legacy_channel_endpoint_service(settings).create(endpoint)).model_dump()
    )
    if base_inputs.create_binding:
        put_matching_binding(
            settings=settings,
            binding_id=saved.endpoint_id,
            transport="telegram_user",
            profile_id=saved.profile_id,
            session_policy=base_inputs.session_policy,
            priority=priority,
            enabled=saved.enabled,
            account_id=saved.account_id,
            prompt_overlay=prompt_overlay,
        )
    binding_warning: str | None = None
    policy_warning: str | None = None
    try:
        allowed, reason = asyncio.run(
            evaluate_telethon_profile_policy(
                settings=settings,
                profile_id=saved.profile_id,
            )
        )
        if not allowed and reason:
            policy_warning = reason
    except Exception:
        policy_warning = None
    if not base_inputs.create_binding:
        try:
            existing = run_channel_binding_service_sync(
                settings,
                lambda service: service.get(binding_id=saved.endpoint_id),
            )
            binding_warning = (
                f"Existing binding `{existing.binding_id}` is still active. "
                f"`--no-binding` does not delete it; run `afk profile binding delete {existing.binding_id}` "
                "to disable reactive routing."
            )
        except ChannelBindingServiceError:
            binding_warning = None
    return TelethonCreateResult(
        saved=saved,
        binding_created=base_inputs.create_binding,
        binding_warning=binding_warning,
        policy_warning=policy_warning,
    )


__all__ = ["TelethonCreateResult", "create_telethon_channel"]
