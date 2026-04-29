"""Update-flow support for Telethon channel CLI commands."""

from __future__ import annotations

from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_int,
    resolve_channel_text,
)
from afkbot.cli.commands.channel_shared import (
    collect_channel_access_policy_inputs,
    merge_ingress_batch_config,
    merge_reply_humanization_config,
    normalize_channel_tool_profile,
)
from afkbot.cli.commands.channel_telethon_commands.common import (
    TELETHON_GROUP_INVOCATION_MODES,
    TELETHON_REPLY_MODE_LABEL_OVERRIDES,
    TELETHON_REPLY_MODES,
    normalize_telethon_group_invocation_mode,
    normalize_telethon_reply_mode,
    split_csv_patterns,
)
from afkbot.cli.commands.channel_telethon_commands.update_context import (
    resolve_telethon_update_context,
)
from afkbot.cli.commands.channel_telethon_commands.update_persistence import (
    save_updated_telethon_channel,
)
from afkbot.cli.commands.channel_telethon_commands.watcher import merge_watcher_config
from afkbot.services.channel_routing.contracts import SessionPolicy
from afkbot.services.channels.endpoint_contracts import (
    CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
    CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
    CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
    CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
    CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
    CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
    CHANNEL_INGRESS_BATCH_SIZE_MAX,
    CHANNEL_INGRESS_BATCH_SIZE_MIN,
    CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MAX,
    CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MIN,
    CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MAX,
    CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MIN,
    CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MAX,
    CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MIN,
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
from afkbot.services.channels.tool_profiles import CHANNEL_TOOL_PROFILE_VALUES


def update_telethon_channel(
    *,
    channel_id: str,
    profile_id: str | None,
    credential_profile_key: str | None,
    account_id: str | None,
    reply_mode: str | None,
    private_policy: str | None,
    allow_from: str | None,
    group_policy: str | None,
    groups: str | None,
    group_allow_from: str | None,
    outbound_allow_to: str | None,
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
    yes: bool,
    lang: str | None,
    ru: bool,
    sync_binding: bool,
    session_policy: SessionPolicy | None,
    prompt_overlay: str | None,
    priority: int | None,
) -> TelethonUserEndpointConfig:
    """Update one Telethon endpoint using the current CLI mutation rules."""

    context = resolve_telethon_update_context(
        channel_id=channel_id,
        profile_id=profile_id,
        yes=yes,
        lang=lang,
        ru=ru,
        sync_binding=sync_binding,
        values=(
            profile_id,
            credential_profile_key,
            account_id,
            reply_mode,
            private_policy,
            allow_from,
            group_policy,
            groups,
            group_allow_from,
            outbound_allow_to,
            tool_profile,
            reply_blocked_chat_patterns,
            reply_allowed_chat_patterns,
            group_invocation_mode,
            process_self_commands,
            command_prefix,
            ingress_batch_enabled,
            ingress_debounce_ms,
            ingress_cooldown_sec,
            ingress_max_batch_size,
            ingress_max_buffer_chars,
            humanize_replies,
            humanize_min_delay_ms,
            humanize_max_delay_ms,
            humanize_chars_per_second,
            mark_read_before_reply,
            watcher_enabled,
            watcher_unmuted_only,
            watcher_include_private,
            watcher_include_groups,
            watcher_include_channels,
            watcher_batch_interval_sec,
            watcher_dialog_refresh_interval_sec,
            watcher_max_batch_size,
            watcher_max_buffer_size,
            watcher_max_message_chars,
            watcher_blocked_chat_patterns,
            watcher_allowed_chat_patterns,
            watcher_delivery_transport,
            watcher_delivery_account_id,
            watcher_delivery_peer_id,
            watcher_delivery_credential_profile_key,
        ),
    )
    settings = context.settings
    current = context.current
    interactive = context.interactive
    prompt_language = context.prompt_language
    resolved_reply_mode = (
        normalize_telethon_reply_mode(
            resolve_channel_choice(
                value=None,
                interactive=True,
                prompt_en="Telethon reply mode",
                prompt_ru="Режим ответов Telethon",
                default=current.reply_mode,
                allowed=TELETHON_REPLY_MODES,
                lang=prompt_language,
                detail_en=(
                    "Choose whether the Telegram user account should only ingest messages or also send replies "
                    "back to the same chat."
                ),
                detail_ru=(
                    "Выберите, должен ли Telegram user-аккаунт только принимать сообщения или ещё и отвечать "
                    "обратно в тот же чат."
                ),
                label_overrides=TELETHON_REPLY_MODE_LABEL_OVERRIDES,
            )
        )
        if interactive
        else normalize_telethon_reply_mode(reply_mode or current.reply_mode)
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
                detail_en=(
                    "Choose the tool set visible from this channel. This cannot grant more than the profile "
                    "allows; it only narrows the profile ceiling."
                ),
                detail_ru=(
                    "Выберите набор инструментов, видимый из этого канала. Это не может дать больше прав, "
                    "чем разрешает профиль; настройка только сужает потолок профиля."
                ),
            )
        )
        if interactive
        else normalize_channel_tool_profile(tool_profile or current.tool_profile)
    )
    resolved_access_policy = collect_channel_access_policy_inputs(
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
    )
    resolved_group_invocation_mode = (
        normalize_telethon_group_invocation_mode(
            resolve_channel_choice(
                value=None,
                interactive=True,
                prompt_en="Telethon group invocation mode",
                prompt_ru="Режим вызова Telethon в группах",
                default=current.group_invocation_mode,
                allowed=TELETHON_GROUP_INVOCATION_MODES,
                lang=prompt_language,
                detail_en=(
                    "Choose which group messages may start an agent turn: replies to the account, command-prefix "
                    "messages, or every allowed message."
                ),
                detail_ru=(
                    "Выберите, какие сообщения в группах могут запускать ход агента: ответы user-аккаунту, "
                    "сообщения с командным префиксом или каждое разрешённое сообщение."
                ),
            )
        )
        if interactive
        else normalize_telethon_group_invocation_mode(group_invocation_mode or current.group_invocation_mode)
    )
    resolved_process_self_commands = (
        resolve_channel_bool(
            value=None,
            interactive=True,
            prompt_en="Process self commands?",
            prompt_ru="Обрабатывать собственные команды?",
            default=current.process_self_commands,
            lang=prompt_language,
            detail_en="Enable this only if your own Telegram messages should control the userbot.",
            detail_ru="Включайте только если ваши собственные сообщения в Telegram должны управлять userbot.",
        )
        if interactive
        else (current.process_self_commands if process_self_commands is None else process_self_commands)
    )
    resolved_command_prefix = (
        resolve_channel_text(
            value=None,
            interactive=True,
            prompt_en="Command prefix",
            prompt_ru="Префикс команды",
            default=current.command_prefix,
            lang=prompt_language,
        )
        if interactive
        else (command_prefix or current.command_prefix)
    )
    resolved_ingress_enabled = (
        resolve_channel_bool(
            value=None,
            interactive=True,
            prompt_en="Merge message bursts before replying?",
            prompt_ru="Объединять всплески сообщений перед ответом?",
            default=current.ingress_batch.enabled,
            lang=prompt_language,
            detail_en=(
                "When enabled, AFKBOT waits briefly after new messages and sends one combined prompt "
                "to the agent."
            ),
            detail_ru=(
                "Если включить, AFKBOT коротко ждёт после новых сообщений и отправляет агенту один "
                "объединённый запрос."
            ),
        )
        if interactive
        else (current.ingress_batch.enabled if ingress_batch_enabled is None else ingress_batch_enabled)
    )
    resolved_ingress_batch = merge_ingress_batch_config(
        current=current.ingress_batch,
        enabled=resolved_ingress_enabled,
        debounce_ms=(
            resolve_channel_int(
                value=None,
                interactive=True,
                prompt_en="Quiet window before merge (ms)",
                prompt_ru="Окно тишины перед объединением (мс)",
                default=current.ingress_batch.debounce_ms,
                lang=prompt_language,
                min_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
                max_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
            )
            if interactive and resolved_ingress_enabled
            else (
                resolve_channel_int(
                    value=ingress_debounce_ms,
                    interactive=False,
                    prompt_en="Quiet window before merge (ms)",
                    prompt_ru="Окно тишины перед объединением (мс)",
                    default=current.ingress_batch.debounce_ms,
                    lang=prompt_language,
                    min_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MIN,
                    max_value=CHANNEL_INGRESS_BATCH_DEBOUNCE_MS_MAX,
                )
                if ingress_debounce_ms is not None
                else None
            )
        ),
        cooldown_sec=(
            resolve_channel_int(
                value=None,
                interactive=True,
                prompt_en="Pause after each merged turn (sec)",
                prompt_ru="Пауза после каждого объединённого хода (сек)",
                default=current.ingress_batch.cooldown_sec,
                lang=prompt_language,
                min_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
                max_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
            )
            if interactive and resolved_ingress_enabled
            else (
                resolve_channel_int(
                    value=ingress_cooldown_sec,
                    interactive=False,
                    prompt_en="Pause after each merged turn (sec)",
                    prompt_ru="Пауза после каждого объединённого хода (сек)",
                    default=current.ingress_batch.cooldown_sec,
                    lang=prompt_language,
                    min_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MIN,
                    max_value=CHANNEL_INGRESS_BATCH_COOLDOWN_SEC_MAX,
                )
                if ingress_cooldown_sec is not None
                else None
            )
        ),
        max_batch_size=(
            resolve_channel_int(
                value=None,
                interactive=True,
                prompt_en="Maximum messages per merged turn",
                prompt_ru="Максимум сообщений в одном объединённом ходе",
                default=current.ingress_batch.max_batch_size,
                lang=prompt_language,
                min_value=CHANNEL_INGRESS_BATCH_SIZE_MIN,
                max_value=CHANNEL_INGRESS_BATCH_SIZE_MAX,
            )
            if interactive and resolved_ingress_enabled
            else (
                resolve_channel_int(
                    value=ingress_max_batch_size,
                    interactive=False,
                    prompt_en="Maximum messages per merged turn",
                    prompt_ru="Максимум сообщений в одном объединённом ходе",
                    default=current.ingress_batch.max_batch_size,
                    lang=prompt_language,
                    min_value=CHANNEL_INGRESS_BATCH_SIZE_MIN,
                    max_value=CHANNEL_INGRESS_BATCH_SIZE_MAX,
                )
                if ingress_max_batch_size is not None
                else None
            )
        ),
        max_buffer_chars=(
            resolve_channel_int(
                value=None,
                interactive=True,
                prompt_en="Maximum merged text size (chars)",
                prompt_ru="Максимальный размер объединённого текста (символы)",
                default=current.ingress_batch.max_buffer_chars,
                lang=prompt_language,
                min_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
                max_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
            )
            if interactive and resolved_ingress_enabled
            else (
                resolve_channel_int(
                    value=ingress_max_buffer_chars,
                    interactive=False,
                    prompt_en="Maximum merged text size (chars)",
                    prompt_ru="Максимальный размер объединённого текста (символы)",
                    default=current.ingress_batch.max_buffer_chars,
                    lang=prompt_language,
                    min_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MIN,
                    max_value=CHANNEL_INGRESS_BATCH_BUFFER_CHARS_MAX,
                )
                if ingress_max_buffer_chars is not None
                else None
            )
        ),
    )
    resolved_humanize_enabled = (
        resolve_channel_bool(
            value=None,
            interactive=True,
            prompt_en="Make replies look more natural?",
            prompt_ru="Делать ответы более похожими на живую переписку?",
            default=current.reply_humanization.enabled,
            lang=prompt_language,
            detail_en="Show read receipts, typing indicators, and small delays before replies.",
            detail_ru="Показывать отметки прочтения, индикатор печати и небольшие задержки перед ответами.",
        )
        if interactive
        else (current.reply_humanization.enabled if humanize_replies is None else humanize_replies)
    )
    resolved_reply_humanization = merge_reply_humanization_config(
        current=current.reply_humanization,
        enabled=resolved_humanize_enabled,
        min_delay_ms=(
            resolve_channel_int(
                value=None if interactive else humanize_min_delay_ms,
                interactive=interactive,
                prompt_en="Minimum reply delay (ms)",
                prompt_ru="Минимальная задержка ответа (мс)",
                default=current.reply_humanization.min_delay_ms,
                lang=prompt_language,
                min_value=CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MIN,
                max_value=CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MAX,
            )
            if resolved_humanize_enabled
            else None
        ),
        max_delay_ms=(
            resolve_channel_int(
                value=None if interactive else humanize_max_delay_ms,
                interactive=interactive,
                prompt_en="Maximum reply delay (ms)",
                prompt_ru="Максимальная задержка ответа (мс)",
                default=current.reply_humanization.max_delay_ms,
                lang=prompt_language,
                min_value=CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MIN,
                max_value=CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MAX,
            )
            if resolved_humanize_enabled
            else None
        ),
        chars_per_second=(
            resolve_channel_int(
                value=None if interactive else humanize_chars_per_second,
                interactive=interactive,
                prompt_en="Typing speed (chars/sec)",
                prompt_ru="Скорость печати (символов/сек)",
                default=current.reply_humanization.chars_per_second,
                lang=prompt_language,
                min_value=CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MIN,
                max_value=CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MAX,
            )
            if resolved_humanize_enabled
            else None
        ),
    )
    resolved_mark_read_before_reply = (
        resolve_channel_bool(
            value=None,
            interactive=True,
            prompt_en="Mark chat as read before reply?",
            prompt_ru="Отмечать чат прочитанным перед ответом?",
            default=current.mark_read_before_reply,
            lang=prompt_language,
            detail_en="Send a read receipt before replying.",
            detail_ru="Отправлять отметку о прочтении перед ответом.",
        )
        if interactive
        else (current.mark_read_before_reply if mark_read_before_reply is None else mark_read_before_reply)
    )
    resolved_watcher_enabled = (
        resolve_channel_bool(
            value=None,
            interactive=True,
            prompt_en="Enable watcher digests?",
            prompt_ru="Включить дайджесты наблюдателя?",
            default=current.watcher.enabled,
            lang=prompt_language,
            detail_en=(
                "Watcher mode collects activity from selected dialogs and sends periodic digest turns "
                "to the agent."
            ),
            detail_ru=(
                "Режим наблюдателя собирает активность из выбранных диалогов и периодически отправляет "
                "агенту дайджесты."
            ),
        )
        if interactive
        else (current.watcher.enabled if watcher_enabled is None else watcher_enabled)
    )
    resolved_watcher = merge_watcher_config(
        current=current.watcher,
        enabled=resolved_watcher_enabled,
        unmuted_only=(
            resolve_channel_bool(
                value=None,
                interactive=True,
                prompt_en="Watch only dialogs with notifications on?",
                prompt_ru="Следить только за диалогами с включёнными уведомлениями?",
                default=current.watcher.unmuted_only,
                lang=prompt_language,
            )
            if interactive and resolved_watcher_enabled
            else watcher_unmuted_only
        ),
        include_private=(
            resolve_channel_bool(
                value=None,
                interactive=True,
                prompt_en="Include private chats in digests?",
                prompt_ru="Включать личные чаты в дайджесты?",
                default=current.watcher.include_private,
                lang=prompt_language,
            )
            if interactive and resolved_watcher_enabled
            else watcher_include_private
        ),
        include_groups=(
            resolve_channel_bool(
                value=None,
                interactive=True,
                prompt_en="Include groups in digests?",
                prompt_ru="Включать группы в дайджесты?",
                default=current.watcher.include_groups,
                lang=prompt_language,
            )
            if interactive and resolved_watcher_enabled
            else watcher_include_groups
        ),
        include_channels=(
            resolve_channel_bool(
                value=None,
                interactive=True,
                prompt_en="Include Telegram channels in digests?",
                prompt_ru="Включать Telegram-каналы в дайджесты?",
                default=current.watcher.include_channels,
                lang=prompt_language,
            )
            if interactive and resolved_watcher_enabled
            else watcher_include_channels
        ),
        batch_interval_sec=(
            resolve_channel_int(
                value=None if interactive else watcher_batch_interval_sec,
                interactive=interactive,
                prompt_en="Digest interval (sec)",
                prompt_ru="Интервал дайджеста (сек)",
                default=current.watcher.batch_interval_sec,
                lang=prompt_language,
                min_value=TELETHON_WATCHER_BATCH_INTERVAL_SEC_MIN,
                max_value=TELETHON_WATCHER_BATCH_INTERVAL_SEC_MAX,
            )
            if resolved_watcher_enabled
            else None
        ),
        dialog_refresh_interval_sec=(
            resolve_channel_int(
                value=None if interactive else watcher_dialog_refresh_interval_sec,
                interactive=interactive,
                prompt_en="Dialog metadata refresh interval (sec)",
                prompt_ru="Интервал обновления данных диалогов (сек)",
                default=current.watcher.dialog_refresh_interval_sec,
                lang=prompt_language,
                min_value=TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MIN,
                max_value=TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MAX,
            )
            if resolved_watcher_enabled
            else None
        ),
        max_batch_size=(
            resolve_channel_int(
                value=None if interactive else watcher_max_batch_size,
                interactive=interactive,
                prompt_en="Maximum events per digest",
                prompt_ru="Максимум событий в одном дайджесте",
                default=current.watcher.max_batch_size,
                lang=prompt_language,
                min_value=TELETHON_WATCHER_BATCH_SIZE_MIN,
                max_value=TELETHON_WATCHER_BATCH_SIZE_MAX,
            )
            if resolved_watcher_enabled
            else None
        ),
        max_buffer_size=(
            resolve_channel_int(
                value=None if interactive else watcher_max_buffer_size,
                interactive=interactive,
                prompt_en="Maximum watcher backlog",
                prompt_ru="Максимальный backlog наблюдателя",
                default=current.watcher.max_buffer_size,
                lang=prompt_language,
                min_value=TELETHON_WATCHER_BUFFER_SIZE_MIN,
                max_value=TELETHON_WATCHER_BUFFER_SIZE_MAX,
            )
            if resolved_watcher_enabled
            else None
        ),
        max_message_chars=(
            resolve_channel_int(
                value=None if interactive else watcher_max_message_chars,
                interactive=interactive,
                prompt_en="Maximum characters per watched message",
                prompt_ru="Максимум символов на одно наблюдаемое сообщение",
                default=current.watcher.max_message_chars,
                lang=prompt_language,
                min_value=TELETHON_WATCHER_MESSAGE_CHARS_MIN,
                max_value=TELETHON_WATCHER_MESSAGE_CHARS_MAX,
            )
            if resolved_watcher_enabled
            else None
        ),
        blocked_chat_patterns=(
            None if watcher_blocked_chat_patterns is None else split_csv_patterns(watcher_blocked_chat_patterns)
        ),
        allowed_chat_patterns=(
            None if watcher_allowed_chat_patterns is None else split_csv_patterns(watcher_allowed_chat_patterns)
        ),
        delivery_transport=watcher_delivery_transport,
        delivery_account_id=watcher_delivery_account_id,
        delivery_peer_id=watcher_delivery_peer_id,
        delivery_credential_profile_key=watcher_delivery_credential_profile_key,
    )
    return save_updated_telethon_channel(
        settings=settings,
        current=current,
        profile_id=context.resolved_profile_id,
        credential_profile_key=credential_profile_key,
        account_id=account_id,
        reply_mode=resolved_reply_mode,
        tool_profile=resolved_tool_profile,
        access_policy=resolved_access_policy,
        reply_blocked_chat_patterns=reply_blocked_chat_patterns,
        reply_allowed_chat_patterns=reply_allowed_chat_patterns,
        group_invocation_mode=resolved_group_invocation_mode,
        process_self_commands=resolved_process_self_commands,
        command_prefix=resolved_command_prefix,
        ingress_batch=resolved_ingress_batch,
        reply_humanization=resolved_reply_humanization,
        mark_read_before_reply=resolved_mark_read_before_reply,
        watcher=resolved_watcher,
        prompt_language=prompt_language,
        sync_binding=sync_binding,
        session_policy=session_policy,
        prompt_overlay=prompt_overlay,
        priority=priority,
    )


__all__ = ["update_telethon_channel"]
