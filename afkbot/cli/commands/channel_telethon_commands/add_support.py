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
    build_generated_channel_id,
    build_ingress_batch_config,
    build_reply_humanization_config,
    collect_channel_add_base_inputs,
    collect_channel_access_policy_inputs,
    put_access_policy_bindings,
    render_channel_add_intro,
    should_collect_channel_add_interactively,
)
from afkbot.cli.commands.channel_telethon_commands.common import (
    TELETHON_GROUP_INVOCATION_MODES,
    TELETHON_REPLY_MODE_LABEL_OVERRIDES,
    TELETHON_REPLY_MODES,
    normalize_telethon_group_invocation_mode,
    normalize_telethon_reply_mode,
    split_csv_patterns,
)
from afkbot.cli.commands.channel_telethon_commands.legacy import (
    get_legacy_channel_endpoint_service,
)
from afkbot.cli.commands.channel_telethon_commands.watcher import build_watcher_config
from afkbot.cli.presentation.setup_prompts import resolve_prompt_language
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
    prompt_language = resolve_prompt_language(settings=settings, value=lang, ru=ru)
    generated_channel_id = build_generated_channel_id(transport="telethon")
    if interactive:
        render_channel_add_intro(
            transport="telethon",
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
        binding_session_policy_default="per-chat",
        binding_session_policy_allowed=("main", "per-chat", "per-thread", "per-user-in-group"),
        generated_channel_id=generated_channel_id,
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
            detail_en=(
                "Choose whether the Telegram user account should only ingest messages or also send replies "
                "back to the same chat. Keep `disabled` for read-only monitoring."
            ),
            detail_ru=(
                "Выберите, должен ли Telegram user-аккаунт только принимать сообщения или ещё и отвечать "
                "обратно в тот же чат. Для режима только чтения оставьте `disabled`."
            ),
            label_overrides=TELETHON_REPLY_MODE_LABEL_OVERRIDES,
        )
    )
    access_policy = collect_channel_access_policy_inputs(
        interactive=interactive,
        lang=prompt_language,
        private_policy=private_policy,
        allow_from=allow_from,
        group_policy=group_policy,
        groups=groups,
        group_allow_from=group_allow_from,
        outbound_allow_to=outbound_allow_to,
        tool_profile=base_inputs.tool_profile,
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
    resolved_process_self_commands = resolve_channel_bool(
        value=process_self_commands,
        interactive=interactive,
        prompt_en="Process self commands?",
        prompt_ru="Обрабатывать собственные команды?",
        default=False,
        lang=prompt_language,
        detail_en="Enable this only if your own Telegram messages should control the userbot, for example `.afk status`.",
        detail_ru=(
            "Включайте только если ваши собственные сообщения в Telegram должны управлять userbot, "
            "например `.afk status`."
        ),
    )
    resolved_command_prefix = resolve_channel_text(
        value=command_prefix,
        interactive=interactive and resolved_process_self_commands,
        prompt_en="Command prefix",
        prompt_ru="Префикс команды",
        default=".afk",
        lang=prompt_language,
        detail_en="Command prefix for messages sent from your own Telegram account, for example `.afk status`.",
        detail_ru="Префикс команд из вашего Telegram-аккаунта, например `.afk status`.",
    )
    resolved_ingress_enabled = resolve_channel_bool(
        value=ingress_batch_enabled,
        interactive=interactive,
        prompt_en="Merge message bursts before replying?",
        prompt_ru="Объединять всплески сообщений перед ответом?",
        default=False,
        lang=prompt_language,
        detail_en=(
            "When enabled, AFKBOT waits briefly after new messages and sends one combined prompt to the "
            "agent. This helps when chats arrive in bursts."
        ),
        detail_ru=(
            "Если включить, AFKBOT коротко ждёт после новых сообщений и отправляет агенту один "
            "объединённый запрос. Это полезно, когда сообщения приходят всплесками."
        ),
    )
    resolved_ingress_batch = build_ingress_batch_config(
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
                detail_en="How long AFKBOT waits after the latest inbound message before it sends the merged turn.",
                detail_ru="Сколько AFKBOT ждёт после последнего входящего сообщения перед отправкой объединённого хода.",
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
                detail_en="Optional extra pause per chat after one merged turn is processed. Keep 0 for normal real-time behavior.",
                detail_ru="Необязательная пауза на чат после обработки объединённого хода. Для обычных быстрых ответов оставьте 0.",
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
                detail_en="Safety cap on how many inbound messages may be merged before AFKBOT flushes immediately.",
                detail_ru="Защитный лимит: сколько входящих сообщений можно объединить до немедленной отправки агенту.",
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
                detail_en="Safety cap on the total text kept for one merged turn.",
                detail_ru="Защитный лимит на общий размер текста, который попадёт в один объединённый ход.",
            ),
        )
    resolved_humanize_replies = resolve_channel_bool(
        value=humanize_replies,
        interactive=interactive,
        prompt_en="Make replies look more natural?",
        prompt_ru="Делать ответы более похожими на живую переписку?",
        default=False,
        lang=prompt_language,
        detail_en=(
            "Show read receipts, typing indicators, and small delays before replies. Disable this when fastest "
            "possible behavior matters more than a human-like pace."
        ),
        detail_ru=(
            "Показывать отметки прочтения, индикатор печати и небольшие задержки перед ответами. "
            "Выключите, если важнее максимальная скорость."
        ),
    )
    resolved_reply_humanization = build_reply_humanization_config(
        enabled=resolved_humanize_replies,
            min_delay_ms=resolve_channel_int(
                value=humanize_min_delay_ms,
                interactive=interactive and resolved_humanize_replies,
                prompt_en="Minimum reply delay (ms)",
                prompt_ru="Минимальная задержка ответа (мс)",
                default=1000,
                lang=prompt_language,
                min_value=0,
                detail_en="Shortest delay before AFKBOT sends a reply.",
                detail_ru="Минимальная задержка перед отправкой ответа.",
            ),
            max_delay_ms=resolve_channel_int(
                value=humanize_max_delay_ms,
                interactive=interactive and resolved_humanize_replies,
                prompt_en="Maximum reply delay (ms)",
                prompt_ru="Максимальная задержка ответа (мс)",
                default=8000,
                lang=prompt_language,
                min_value=0,
                detail_en="Maximum delay before sending a reply. Longer replies scale toward this cap.",
                detail_ru="Максимальная задержка перед отправкой ответа. Более длинные ответы стремятся к этому пределу.",
            ),
            chars_per_second=resolve_channel_int(
                value=humanize_chars_per_second,
                interactive=interactive and resolved_humanize_replies,
                prompt_en="Typing speed (chars/sec)",
                prompt_ru="Скорость печати (символов/сек)",
                default=12,
                lang=prompt_language,
                min_value=1,
                detail_en="Approximate typing speed used to turn reply length into a delay.",
                detail_ru="Примерная скорость печати, по которой длина ответа превращается в задержку.",
            ),
        )
    resolved_mark_read_before_reply = resolve_channel_bool(
        value=mark_read_before_reply,
        interactive=interactive,
        prompt_en="Mark chat as read before reply?",
        prompt_ru="Отмечать чат прочитанным перед ответом?",
        default=True,
        lang=prompt_language,
        detail_en=(
            "Send a read receipt before replying. Keep this on if the account should behave like a normal user; "
            "turn it off if read receipts are undesirable."
        ),
        detail_ru=(
            "Отправлять отметку о прочтении перед ответом. Оставьте включённым, если аккаунт должен вести себя "
            "как обычный пользователь; выключите, если отметки прочтения нежелательны."
        ),
    )
    resolved_watcher_enabled = resolve_channel_bool(
        value=watcher_enabled,
        interactive=interactive,
        prompt_en="Enable watcher digests?",
        prompt_ru="Включить дайджесты наблюдателя?",
        default=False,
        lang=prompt_language,
        detail_en=(
            "Watcher mode collects activity from selected dialogs and sends periodic digest turns to the agent "
            "instead of replying directly inside those chats."
        ),
        detail_ru=(
            "Режим наблюдателя собирает активность из выбранных диалогов и периодически отправляет агенту "
            "дайджесты вместо прямых ответов внутри этих чатов."
        ),
    )
    resolved_watcher = build_watcher_config(
        enabled=resolved_watcher_enabled,
        unmuted_only=resolve_channel_bool(
            value=watcher_unmuted_only,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Watch only dialogs with notifications on?",
            prompt_ru="Следить только за диалогами с включёнными уведомлениями?",
            default=True,
            lang=prompt_language,
            detail_en="Track only dialogs whose notifications are not muted. This is a good default to ignore muted backlog.",
            detail_ru="Отслеживать только диалоги без mute. Это хороший вариант по умолчанию, чтобы игнорировать заглушенный backlog.",
        ),
        include_private=resolve_channel_bool(
            value=watcher_include_private,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Include private chats in digests?",
            prompt_ru="Включать личные чаты в дайджесты?",
            default=True,
            lang=prompt_language,
            detail_en="Include one-to-one chats when building watcher digests.",
            detail_ru="Включать личные диалоги при сборке дайджестов наблюдателя.",
        ),
        include_groups=resolve_channel_bool(
            value=watcher_include_groups,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Include groups in digests?",
            prompt_ru="Включать группы в дайджесты?",
            default=True,
            lang=prompt_language,
            detail_en="Include groups and supergroups when building watcher digests.",
            detail_ru="Включать группы и супергруппы при сборке дайджестов наблюдателя.",
        ),
        include_channels=resolve_channel_bool(
            value=watcher_include_channels,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Include Telegram channels in digests?",
            prompt_ru="Включать Telegram-каналы в дайджесты?",
            default=True,
            lang=prompt_language,
            detail_en="Include channel posts when building watcher digests.",
            detail_ru="Включать посты Telegram-каналов при сборке дайджестов наблюдателя.",
        ),
        batch_interval_sec=resolve_channel_int(
            value=watcher_batch_interval_sec,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Digest interval (sec)",
            prompt_ru="Интервал дайджеста (сек)",
            default=300,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_BATCH_INTERVAL_SEC_MIN,
            max_value=TELETHON_WATCHER_BATCH_INTERVAL_SEC_MAX,
            detail_en="How often watcher sends collected events as one digest turn.",
            detail_ru="Как часто наблюдатель отправляет накопленные события одним дайджестом.",
        ),
        dialog_refresh_interval_sec=resolve_channel_int(
            value=watcher_dialog_refresh_interval_sec,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Dialog metadata refresh interval (sec)",
            prompt_ru="Интервал обновления данных диалогов (сек)",
            default=300,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MIN,
            max_value=TELETHON_WATCHER_REFRESH_INTERVAL_SEC_MAX,
            detail_en="How often watcher refreshes dialog metadata such as titles and mute state.",
            detail_ru="Как часто наблюдатель обновляет данные диалогов: названия, mute-состояние и похожие признаки.",
        ),
        max_batch_size=resolve_channel_int(
            value=watcher_max_batch_size,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Maximum events per digest",
            prompt_ru="Максимум событий в одном дайджесте",
            default=100,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_BATCH_SIZE_MIN,
            max_value=TELETHON_WATCHER_BATCH_SIZE_MAX,
            detail_en="Maximum number of watched events included in one digest turn.",
            detail_ru="Максимальное количество событий, которое попадёт в один дайджест.",
        ),
        max_buffer_size=resolve_channel_int(
            value=watcher_max_buffer_size,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Maximum watcher backlog",
            prompt_ru="Максимальный backlog наблюдателя",
            default=500,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_BUFFER_SIZE_MIN,
            max_value=TELETHON_WATCHER_BUFFER_SIZE_MAX,
            detail_en="Maximum in-memory watcher backlog before the oldest events start dropping.",
            detail_ru="Максимальный backlog наблюдателя в памяти перед удалением самых старых событий.",
        ),
        max_message_chars=resolve_channel_int(
            value=watcher_max_message_chars,
            interactive=interactive and resolved_watcher_enabled,
            prompt_en="Maximum characters per watched message",
            prompt_ru="Максимум символов на одно наблюдаемое сообщение",
            default=1000,
            lang=prompt_language,
            min_value=TELETHON_WATCHER_MESSAGE_CHARS_MIN,
            max_value=TELETHON_WATCHER_MESSAGE_CHARS_MAX,
            detail_en="Per-message clip length before watcher truncates message bodies in digests.",
            detail_ru="Лимит длины одного сообщения перед обрезкой текста в дайджесте.",
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
        access_policy=access_policy,
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
        binding_count = put_access_policy_bindings(
            settings=settings,
            endpoint_id=saved.endpoint_id,
            transport="telegram_user",
            profile_id=saved.profile_id,
            session_policy=base_inputs.session_policy,
            priority=priority,
            enabled=saved.enabled,
            account_id=saved.account_id,
            prompt_overlay=prompt_overlay,
            access_policy=saved.access_policy,
        )
    else:
        binding_count = 0
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
        binding_created=base_inputs.create_binding and binding_count > 0,
        binding_warning=binding_warning,
        policy_warning=policy_warning,
    )


__all__ = ["TelethonCreateResult", "create_telethon_channel"]
