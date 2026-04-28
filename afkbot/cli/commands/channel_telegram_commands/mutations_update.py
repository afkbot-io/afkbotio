"""Update-command workflow for Telegram channel CLI operations."""

from __future__ import annotations

import json

import typer

from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_int,
    resolve_channel_text,
)
from afkbot.cli.commands.channel_shared import (
    collect_channel_access_policy_inputs,
    load_channel_profile,
    merge_ingress_batch_config,
    merge_reply_humanization_config,
    normalize_channel_tool_profile,
    put_access_policy_bindings,
    resolve_binding_update_inputs,
    resolve_channel_update_profile_id,
    should_collect_channel_update_interactively,
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
    CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MAX,
    CHANNEL_REPLY_HUMANIZATION_CHARS_PER_SECOND_MIN,
    CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MAX,
    CHANNEL_REPLY_HUMANIZATION_MAX_DELAY_MS_MIN,
    CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MAX,
    CHANNEL_REPLY_HUMANIZATION_MIN_DELAY_MS_MIN,
    TelegramPollingEndpointConfig,
)
from afkbot.services.channels.tool_profiles import CHANNEL_TOOL_PROFILE_VALUES


def run_telegram_update(
    *,
    runtime: TelegramCommandRuntime,
    channel_id: str,
    profile_id: str | None,
    credential_profile_key: str | None,
    account_id: str | None,
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
    yes: bool,
    lang: str | None,
    ru: bool,
    sync_binding: bool,
    session_policy: SessionPolicy | None,
    prompt_overlay: str | None,
    priority: int | None,
    json_output: bool,
) -> None:
    """Update one Telegram polling endpoint without implicit upsert."""

    binding_count = 0
    try:
        current = runtime.load_endpoint(channel_id)
        prompt_language = resolve_prompt_language(settings=runtime.settings, value=lang, ru=ru)
        interactive = should_collect_channel_update_interactively(
            yes=yes,
            sync_binding=sync_binding,
            values=(
                profile_id,
                credential_profile_key,
                account_id,
                group_trigger_mode,
                private_policy,
                allow_from,
                group_policy,
                groups,
                group_allow_from,
                outbound_allow_to,
                tool_profile,
                ingress_batch_enabled,
                ingress_debounce_ms,
                ingress_cooldown_sec,
                ingress_max_batch_size,
                ingress_max_buffer_chars,
                humanize_replies,
                humanize_min_delay_ms,
                humanize_max_delay_ms,
                humanize_chars_per_second,
            ),
        )
        resolved_profile_id_for_validation = resolve_channel_update_profile_id(
            profile_id=profile_id,
            current_profile_id=current.profile_id,
        )
        load_channel_profile(
            settings=runtime.settings,
            profile_id=resolved_profile_id_for_validation,
        )
        resolved_group_trigger_mode = (
            normalize_telegram_group_trigger_mode(
                resolve_channel_choice(
                    value=None,
                    interactive=True,
                    prompt_en="Telegram group trigger mode",
                    prompt_ru="Режим триггера для Telegram групп",
                    default=current.group_trigger_mode,
                    allowed=TELEGRAM_GROUP_TRIGGER_MODES,
                    lang=prompt_language,
                )
            )
            if interactive
            else normalize_telegram_group_trigger_mode(group_trigger_mode or current.group_trigger_mode)
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
        resolved_access_policy = collect_channel_access_policy_inputs(
            interactive=interactive,
            lang=prompt_language,
            private_policy=private_policy,
            allow_from=allow_from,
            group_policy=group_policy,
            groups=groups,
            group_allow_from=group_allow_from,
            outbound_allow_to=outbound_allow_to,
            private_policy_default=current.access_policy.private_policy,
            allow_from_default=current.access_policy.allow_from,
            group_policy_default=current.access_policy.group_policy,
            groups_default=current.access_policy.groups,
            group_allow_from_default=current.access_policy.group_allow_from,
            outbound_allow_to_default=current.access_policy.outbound_allow_to,
        )
        resolved_ingress_enabled = (
            resolve_channel_bool(
                value=None,
                interactive=True,
                prompt_en="Enable ingress batching?",
                prompt_ru="Включить batching входящих сообщений?",
                default=current.ingress_batch.enabled,
                lang=prompt_language,
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
                    prompt_en="Ingress debounce (ms)",
                    prompt_ru="Ingress debounce (мс)",
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
                        prompt_en="Ingress debounce (ms)",
                        prompt_ru="Ingress debounce (мс)",
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
                    prompt_en="Ingress cooldown (sec)",
                    prompt_ru="Ingress cooldown (сек)",
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
                        prompt_en="Ingress cooldown (sec)",
                        prompt_ru="Ingress cooldown (сек)",
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
                    prompt_en="Ingress max batch size",
                    prompt_ru="Максимальный размер ingress batch",
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
                        prompt_en="Ingress max batch size",
                        prompt_ru="Максимальный размер ingress batch",
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
                    prompt_en="Ingress max buffer chars",
                    prompt_ru="Максимальный размер ingress buffer в символах",
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
                        prompt_en="Ingress max buffer chars",
                        prompt_ru="Максимальный размер ingress buffer в символах",
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
                prompt_en="Enable humanized replies?",
                prompt_ru="Включить humanized replies?",
                default=current.reply_humanization.enabled,
                lang=prompt_language,
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
                    prompt_en="Humanized min delay (ms)",
                    prompt_ru="Минимальная задержка humanized replies (мс)",
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
                    prompt_en="Humanized max delay (ms)",
                    prompt_ru="Максимальная задержка humanized replies (мс)",
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
        endpoint = TelegramPollingEndpointConfig(
            endpoint_id=current.endpoint_id,
            profile_id=resolved_profile_id_for_validation,
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
            group_trigger_mode=resolved_group_trigger_mode,
            tool_profile=resolved_tool_profile,
            access_policy=resolved_access_policy,
            ingress_batch=resolved_ingress_batch,
            reply_humanization=resolved_reply_humanization,
        )
        saved = runtime.update_endpoint(endpoint)
        if sync_binding:
            resolved_binding_inputs = resolve_binding_update_inputs(
                settings=runtime.settings,
                binding_id=saved.endpoint_id,
                session_policy=session_policy,
                session_policy_default="per-thread",
                priority=priority,
                prompt_overlay=prompt_overlay,
            )
            binding_count = put_access_policy_bindings(
                settings=runtime.settings,
                endpoint_id=saved.endpoint_id,
                transport="telegram",
                profile_id=saved.profile_id,
                session_policy=resolved_binding_inputs.session_policy,
                priority=resolved_binding_inputs.priority,
                enabled=saved.enabled,
                account_id=saved.account_id,
                prompt_overlay=resolved_binding_inputs.prompt_overlay,
                access_policy=saved.access_policy,
                replace_existing=True,
            )
    except Exception as exc:
        runtime.raise_error(exc)
        raise AssertionError("unreachable")

    if json_output:
        typer.echo(json.dumps({"channel": saved.model_dump(mode="json")}, ensure_ascii=True))
        runtime.reload_notice(runtime.settings)
        return
    typer.echo(
        f"Telegram channel `{saved.endpoint_id}` updated for profile `{saved.profile_id}` "
        f"(credential_profile={saved.credential_profile_key}, account_id={saved.account_id}, "
        f"group_trigger_mode={saved.group_trigger_mode}, tool_profile={saved.tool_profile}, "
        f"ingress_batch={saved.ingress_batch.enabled}, "
        f"humanize_replies={saved.reply_humanization.enabled}, enabled={saved.enabled})."
    )
    if sync_binding:
        typer.echo(f"Matching bindings updated: {binding_count}.")
    runtime.reload_notice(runtime.settings)


__all__ = ["run_telegram_update"]
