"""Add-command workflow for Telegram channel CLI operations."""

from __future__ import annotations

import json
from collections.abc import Callable

import typer

from afkbot.cli.commands.channel_credentials_support import configure_telegram_channel_credentials
from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_int,
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
from afkbot.cli.commands.channel_telegram_commands.common import (
    TELEGRAM_GROUP_TRIGGER_MODES,
    normalize_telegram_group_trigger_mode,
)
from afkbot.cli.commands.channel_telegram_commands.runtime import TelegramCommandRuntime
from afkbot.cli.presentation.setup_prompts import resolve_prompt_language
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
    TelegramPollingEndpointConfig,
)


def run_telegram_add(
    *,
    runtime: TelegramCommandRuntime,
    channel_id: str | None,
    profile_id: str | None,
    credential_profile_key: str | None,
    account_id: str | None,
    enabled: bool | None,
    group_trigger_mode: str | None,
    private_policy: str | None,
    allow_from: str | None,
    group_policy: str | None,
    groups: str | None,
    group_allow_from: str | None,
    outbound_allow_to: str | None,
    tool_profile: str | None,
    ingress_batch_enabled: bool | None,
    ingress_debounce_ms: int | None,
    ingress_cooldown_sec: int | None,
    ingress_max_batch_size: int | None,
    ingress_max_buffer_chars: int | None,
    humanize_replies: bool | None,
    humanize_min_delay_ms: int | None,
    humanize_max_delay_ms: int | None,
    humanize_chars_per_second: int | None,
    create_binding: bool | None,
    session_policy: SessionPolicy | None,
    prompt_overlay: str | None,
    priority: int,
    yes: bool,
    lang: str | None,
    ru: bool,
    json_output: bool,
    configure_credentials: Callable[..., bool] = configure_telegram_channel_credentials,
) -> None:
    """Create one Telegram polling endpoint, optionally with a matching binding."""

    interactive = should_collect_channel_add_interactively(
        yes=yes,
        channel_id=channel_id,
        profile_id=profile_id,
        credential_profile_key=credential_profile_key,
    )
    prompt_language = resolve_prompt_language(settings=runtime.settings, value=lang, ru=ru)
    generated_channel_id = build_generated_channel_id(transport="telegram")
    try:
        if interactive:
            render_channel_add_intro(
                transport="telegram",
                lang=prompt_language,
                suggested_channel_id=generated_channel_id,
            )
        base_inputs = collect_channel_add_base_inputs(
            settings=runtime.settings,
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
            binding_session_policy_allowed=("main", "per-chat", "per-thread", "per-user-in-group"),
            generated_channel_id=generated_channel_id,
        )
        resolved_group_trigger_mode = normalize_telegram_group_trigger_mode(
            resolve_channel_choice(
                value=group_trigger_mode,
                interactive=interactive,
                prompt_en="Telegram group trigger mode",
                prompt_ru="Режим триггера для Telegram групп",
                default="mention_or_reply",
                allowed=TELEGRAM_GROUP_TRIGGER_MODES,
                lang=prompt_language,
                detail_en="Choose when group and supergroup messages are allowed to trigger the bot: on mentions, replies, or every message.",
                detail_ru="Выберите, когда сообщения в группах и супергруппах могут запускать бота: по mentions, по reply или на каждое сообщение.",
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
        )
        resolved_ingress_enabled = resolve_channel_bool(
            value=ingress_batch_enabled,
            interactive=interactive,
            prompt_en="Enable ingress batching?",
            prompt_ru="Включить batching входящих сообщений?",
            default=False,
            lang=prompt_language,
            detail_en="Batching waits for a short quiet window and merges bursts of inbound messages into one turn, which reduces spammy reply loops.",
            detail_ru="Batching ждёт короткое окно тишины и объединяет всплески входящих сообщений в один turn, что уменьшает спамные циклы ответов.",
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
                detail_en="How long AFKBOT waits after the last inbound message before flushing one combined turn.",
                detail_ru="Сколько AFKBOT ждёт после последнего входящего сообщения перед отправкой одного объединённого turn.",
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
                detail_en="Optional extra quiet period per chat after one batch is processed. Keep 0 for normal real-time behavior.",
                detail_ru="Необязательная дополнительная пауза на чат после обработки одного batch. Для обычного real-time поведения оставьте 0.",
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
                detail_en="Safety cap on how many inbound messages may merge into one turn before AFKBOT flushes immediately.",
                detail_ru="Страхующий лимит на количество входящих сообщений, которые можно слить в один turn до немедленного flush.",
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
                detail_en="Safety cap on the total buffered text size for one merged turn.",
                detail_ru="Страхующий лимит на суммарный размер текста, который хранится для одного объединённого turn.",
            ),
        )
        resolved_humanize_replies = resolve_channel_bool(
            value=humanize_replies,
            interactive=interactive,
            prompt_en="Enable humanized replies?",
            prompt_ru="Включить humanized replies?",
            default=False,
            lang=prompt_language,
            detail_en="Show typing indicators and short reply delays so the bot behaves less abruptly. Disable for the fastest possible responses.",
            detail_ru="Показывать typing и добавлять небольшие задержки, чтобы бот отвечал менее резко. Отключите для максимально быстрых ответов.",
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
                detail_en="Shortest delay before sending a reply when humanized replies are enabled.",
                detail_ru="Минимальная задержка перед отправкой ответа, когда включены humanized replies.",
            ),
            max_delay_ms=resolve_channel_int(
                value=humanize_max_delay_ms,
                interactive=interactive and resolved_humanize_replies,
                prompt_en="Humanized max delay (ms)",
                prompt_ru="Максимальная задержка humanized replies (мс)",
                default=8000,
                lang=prompt_language,
                min_value=0,
                detail_en="Maximum delay cap before sending a reply. Longer answers scale toward this ceiling.",
                detail_ru="Верхний предел задержки перед отправкой ответа. Более длинные ответы стремятся к этому потолку.",
            ),
            chars_per_second=resolve_channel_int(
                value=humanize_chars_per_second,
                interactive=interactive and resolved_humanize_replies,
                prompt_en="Typing speed (chars/sec)",
                prompt_ru="Скорость печати (символов/сек)",
                default=12,
                lang=prompt_language,
                min_value=1,
                detail_en="Approximate typing speed used to convert reply length into a delay.",
                detail_ru="Примерная скорость печати, по которой длина ответа переводится в задержку.",
            ),
        )
        endpoint = TelegramPollingEndpointConfig(
            endpoint_id=base_inputs.channel_id,
            profile_id=base_inputs.profile_id,
            credential_profile_key=base_inputs.credential_profile_key,
            account_id=base_inputs.account_id,
            enabled=base_inputs.enabled,
            group_trigger_mode=resolved_group_trigger_mode,
            tool_profile=base_inputs.tool_profile,
            access_policy=access_policy,
            ingress_batch=resolved_ingress_batch,
            reply_humanization=resolved_reply_humanization,
        )
        if interactive and credential_profile_key is None:
            configure_credentials(
                settings=runtime.settings,
                profile_id=base_inputs.profile_id,
                credential_profile_key=base_inputs.credential_profile_key,
                interactive=True,
                lang=prompt_language,
            )
        saved = runtime.create_endpoint(endpoint)
        binding_count = 0
        if base_inputs.create_binding:
            binding_count = put_access_policy_bindings(
                settings=runtime.settings,
                endpoint_id=saved.endpoint_id,
                transport="telegram",
                profile_id=saved.profile_id,
                session_policy=base_inputs.session_policy,
                priority=priority,
                enabled=saved.enabled,
                account_id=saved.account_id,
                prompt_overlay=prompt_overlay,
                access_policy=saved.access_policy,
            )
    except Exception as exc:
        runtime.raise_error(exc)
        raise AssertionError("unreachable")

    if json_output:
        typer.echo(json.dumps({"channel": saved.model_dump(mode="json")}, ensure_ascii=True))
        runtime.reload_notice(runtime.settings)
        return
    typer.echo(
        f"Telegram channel `{saved.endpoint_id}` saved for profile `{saved.profile_id}` "
        f"(credential_profile={saved.credential_profile_key}, account_id={saved.account_id}, "
        f"group_trigger_mode={saved.group_trigger_mode}, tool_profile={saved.tool_profile}, "
        f"ingress_batch={saved.ingress_batch.enabled}, "
        f"humanize_replies={saved.reply_humanization.enabled}, "
        f"enabled={saved.enabled})."
    )
    if base_inputs.create_binding:
        typer.echo(f"Matching bindings created/updated: {binding_count}.")
    runtime.reload_notice(runtime.settings)


__all__ = ["run_telegram_add"]
