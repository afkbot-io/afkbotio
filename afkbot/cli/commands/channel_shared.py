"""Shared CLI helpers for channel endpoint mutation and configuration rendering."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import cast

import typer

from afkbot.cli.commands.channel_prompt_support import (
    resolve_channel_bool,
    resolve_channel_choice,
    resolve_channel_text,
)
from afkbot.cli.presentation.inline_select import select_option_dialog
from afkbot.cli.presentation.prompt_i18n import msg, single_hint
from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.channel_routing import ChannelBindingRule
from afkbot.services.channel_routing.contracts import SessionPolicy
from afkbot.services.channel_routing.service import (
    ChannelBindingServiceError,
    run_channel_binding_service_sync,
)
from afkbot.services.channels.endpoint_contracts import (
    ChannelIngressBatchConfig,
    ChannelReplyHumanizationConfig,
)
from afkbot.services.channels.tool_profiles import (
    ChannelToolProfile,
    CHANNEL_TOOL_PROFILE_VALUES,
    default_channel_tool_profile_for_policy,
)
from afkbot.services.profile_runtime import ProfileDetails, run_profile_service_sync
from afkbot.settings import Settings


@dataclass(frozen=True)
class CollectedChannelAddBaseInputs:
    """Shared base inputs collected by one channel add flow."""

    channel_id: str
    profile_id: str
    credential_profile_key: str
    account_id: str
    enabled: bool
    tool_profile: ChannelToolProfile
    create_binding: bool
    session_policy: SessionPolicy


@dataclass(frozen=True)
class ResolvedBindingUpdateInputs:
    """Shared resolved binding inputs for one channel update flow."""

    session_policy: SessionPolicy
    priority: int
    prompt_overlay: str | None


def should_collect_channel_add_interactively(
    *,
    yes: bool,
    channel_id: str | None,
    profile_id: str | None,
    credential_profile_key: str | None,
) -> bool:
    """Return whether channel add should prompt for missing base values."""

    return not yes and (channel_id is None or profile_id is None or credential_profile_key is None)


def should_collect_channel_update_interactively(
    *,
    yes: bool,
    sync_binding: bool,
    values: tuple[object | None, ...],
) -> bool:
    """Return whether one channel update should prompt using current defaults."""

    return not yes and not sync_binding and all(value is None for value in values)


def build_generated_channel_id(*, transport: str) -> str:
    """Build one safe default channel id for interactive and `--yes` add flows."""

    normalized_transport = transport.strip().lower()
    return f"{normalized_transport}-{secrets.token_hex(4)}"


def render_channel_add_intro(
    *,
    transport: str,
    lang: PromptLanguage,
    suggested_channel_id: str,
) -> None:
    """Render one short operator-facing intro before interactive channel setup."""

    normalized_transport = transport.strip().lower()
    if normalized_transport == "telegram":
        typer.echo(
            msg(
                lang,
                en=(
                    "Telegram Bot API channel setup\n"
                    f"- This wizard creates one polling endpoint for a Telegram bot.\n"
                    f"- `Channel id` is your local AFKBOT id for later `show`, `update`, `status`, and `poll-once` commands. "
                    f"Press Enter there to accept `{suggested_channel_id}`.\n"
                    "- If bot credentials are not already configured, the wizard will ask for the BotFather token.\n"
                    "- Optional: you may also save a default chat id for app-level Telegram actions; leave it blank if you do not need it yet."
                ),
                ru=(
                    "Настройка Telegram Bot API канала\n"
                    f"- Этот мастер создаёт один polling endpoint для Telegram-бота.\n"
                    f"- `Идентификатор канала` это локальный id внутри AFKBOT для команд `show`, `update`, `status` и `poll-once`. "
                    f"На этом вопросе можно просто нажать Enter и принять `{suggested_channel_id}`.\n"
                    "- Если credentials для бота ещё не настроены, мастер попросит BotFather token.\n"
                    "- Дополнительно можно сохранить chat id по умолчанию для app-level Telegram действий; если пока не нужен, оставьте поле пустым."
                ),
            )
        )
        return
    if normalized_transport == "telethon":
        typer.echo(
            msg(
                lang,
                en=(
                    "Telethon user channel setup\n"
                    f"- This wizard creates one Telegram user-account endpoint powered by Telethon.\n"
                    f"- `Channel id` is your local AFKBOT id for later `show`, `update`, `status`, `authorize`, and `dialogs` commands. "
                    f"Press Enter there to accept `{suggested_channel_id}`.\n"
                    "- If Telethon credentials are not already configured, the wizard will ask for API id, API hash, and phone.\n"
                    "- A session string is optional: import it now, or save the channel first and authorize later with `afk channel telethon authorize <channel_id>`."
                ),
                ru=(
                    "Настройка Telethon user-канала\n"
                    f"- Этот мастер создаёт один endpoint Telegram user-аккаунта на Telethon.\n"
                    f"- `Идентификатор канала` это локальный id внутри AFKBOT для команд `show`, `update`, `status`, `authorize` и `dialogs`. "
                    f"На этом вопросе можно просто нажать Enter и принять `{suggested_channel_id}`.\n"
                    "- Если credentials для Telethon ещё не настроены, мастер попросит API id, API hash и телефон.\n"
                    "- Session string необязателен: можно импортировать его сразу или сначала сохранить канал, а авторизоваться потом через `afk channel telethon authorize <channel_id>`."
                ),
            )
        )
        return
    if normalized_transport == "partyflow":
        typer.echo(
            msg(
                lang,
                en=(
                    "PartyFlow webhook channel setup\n"
                    f"- This wizard creates one PartyFlow outgoing-webhook endpoint.\n"
                    f"- `Channel id` is your local AFKBOT id for later `show`, `update`, and `delete` commands. "
                    f"Press Enter there to accept `{suggested_channel_id}`.\n"
                    "- PartyFlow does not support Telegram-style polling here; webhook is the only ingress mode.\n"
                    "- You will need the bot token and the webhook signing secret from PartyFlow UI."
                ),
                ru=(
                    "Настройка PartyFlow webhook-канала\n"
                    f"- Этот мастер создаёт один endpoint для исходящих webhook-событий PartyFlow.\n"
                    f"- `Идентификатор канала` это локальный id внутри AFKBOT для команд `show`, `update` и `delete`. "
                    f"На этом вопросе можно просто нажать Enter и принять `{suggested_channel_id}`.\n"
                    "- Polling в стиле Telegram здесь не поддерживается; сейчас доступен только режим webhook.\n"
                    "- Понадобятся токен бота и секрет подписи webhook из UI PartyFlow."
                ),
            )
        )
        return
    raise ValueError(f"Unsupported channel transport for intro: {transport}")


def collect_channel_add_base_inputs(
    *,
    settings: Settings,
    interactive: bool,
    lang: PromptLanguage,
    channel_id: str | None,
    profile_id: str | None,
    credential_profile_key: str | None,
    account_id: str | None,
    enabled: bool | None,
    tool_profile: str | None,
    create_binding: bool | None,
    session_policy: SessionPolicy | None,
    binding_session_policy_default: SessionPolicy,
    binding_session_policy_allowed: tuple[str, ...],
    generated_channel_id: str,
) -> CollectedChannelAddBaseInputs:
    """Collect shared channel add inputs for any transport family."""

    resolved_channel_id = resolve_channel_text(
        value=channel_id,
        interactive=interactive,
        prompt_en="Channel id",
        prompt_ru="Идентификатор канала",
        default=(generated_channel_id if channel_id is None else None),
        lang=lang,
        normalize_lower=True,
        detail_en=(
            "This is AFKBOT's stable local id for the channel. "
            "It is used later in `afk channel show`, `update`, `status`, and runtime commands. "
            f"Allowed format: lowercase letters, digits, hyphen. Press Enter to accept `{generated_channel_id}`."
        ),
        detail_ru=(
            "Это стабильный локальный id канала внутри AFKBOT. "
            "Он потом используется в `afk channel show`, `update`, `status` и runtime-командах. "
            f"Допустимы строчные буквы, цифры и дефис. Нажмите Enter, чтобы принять `{generated_channel_id}`."
        ),
    )
    resolved_profile_id = resolve_channel_profile_id(
        settings=settings,
        profile_id=profile_id,
        interactive=interactive,
        default="default",
        lang=lang,
    )
    profile = load_channel_profile(settings=settings, profile_id=resolved_profile_id)
    default_tool_profile = default_channel_tool_profile_for_policy(policy=profile.policy)
    resolved_credential_profile_key = normalize_channel_choice_value(
        credential_profile_key or resolved_channel_id
    )
    resolved_account_id = resolve_channel_text(
        value=account_id,
        interactive=False,
        prompt_en="Account id",
        prompt_ru="Account id",
        default=resolved_channel_id,
        lang=lang,
        normalize_lower=True,
    )
    resolved_enabled = resolve_channel_bool(
        value=enabled,
        interactive=interactive,
        prompt_en="Enable channel?",
        prompt_ru="Включить канал?",
        default=True,
        lang=lang,
        detail_en="Disabled channels stay saved in config, but runtime will ignore them until you enable them.",
        detail_ru="Отключенный канал останется в конфиге, но runtime не будет его запускать, пока вы его не включите.",
    )
    resolved_tool_profile = normalize_channel_tool_profile(
        resolve_channel_choice(
            value=tool_profile,
            interactive=interactive,
            prompt_en="Channel tool profile",
            prompt_ru="Профиль инструментов канала",
            default=default_tool_profile,
            allowed=CHANNEL_TOOL_PROFILE_VALUES,
            lang=lang,
            detail_en=(
                "This narrows what the agent may do in this channel. "
                "`inherit` keeps the profile ceiling, `chat_minimal` is reply-only, "
                "`messaging_safe` allows safe memory/app actions, and `support_readonly` adds read-only file work."
            ),
            detail_ru=(
                "Это сужает набор действий агента именно в этом канале. "
                "`inherit` оставляет потолок профиля, `chat_minimal` — только ответы, "
                "`messaging_safe` — безопасные memory/app действия, `support_readonly` — ещё и read-only работу с файлами."
            ),
        )
    )
    resolved_create_binding = resolve_channel_bool(
        value=create_binding,
        interactive=interactive,
        prompt_en="Create matching routing binding?",
        prompt_ru="Создать matching routing binding?",
        default=True,
        lang=lang,
        detail_en=(
            "A binding connects inbound channel events to this profile and defines how sessions are grouped. "
            "Turn it off only if you plan to manage routing manually."
        ),
        detail_ru=(
            "Binding связывает входящие события канала с этим профилем и определяет, как группируются сессии. "
            "Отключайте только если хотите настраивать routing вручную."
        ),
    )
    resolved_session_policy: SessionPolicy = (
        normalize_session_policy(
            resolve_channel_choice(
                value=session_policy,
                interactive=interactive,
                prompt_en="Binding session policy",
                prompt_ru="Политика сессии binding",
                default=binding_session_policy_default,
                allowed=binding_session_policy_allowed,
                lang=lang,
                detail_en=(
                    "This decides how AFKBOT groups messages into one conversation: by whole chat, by thread, "
                    "or by user inside a group."
                ),
                detail_ru=(
                    "Это определяет, как AFKBOT группирует сообщения в одну сессию: по всему чату, по треду "
                    "или по пользователю внутри группы."
                ),
            )
        )
        if resolved_create_binding
        else binding_session_policy_default
    )
    return CollectedChannelAddBaseInputs(
        channel_id=resolved_channel_id,
        profile_id=resolved_profile_id,
        credential_profile_key=resolved_credential_profile_key,
        account_id=resolved_account_id,
        enabled=resolved_enabled,
        tool_profile=resolved_tool_profile,
        create_binding=resolved_create_binding,
        session_policy=resolved_session_policy,
    )


def load_channel_profile(*, settings: Settings, profile_id: str) -> ProfileDetails:
    """Load one profile for channel CLI flows and fail if it does not exist."""

    return run_profile_service_sync(settings, lambda service: service.get(profile_id=profile_id))


def list_channel_profiles(*, settings: Settings) -> tuple[str, ...]:
    """List profile ids available to channel interactive flows."""

    profiles = run_profile_service_sync(settings, lambda service: service.list())
    return tuple(item.id for item in profiles)


def resolve_channel_profile_id(
    *,
    settings: Settings,
    profile_id: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve one profile id using selector-first UX with text fallback."""

    if profile_id is not None:
        return normalize_channel_choice_value(profile_id)
    available = list_channel_profiles(settings=settings)
    if interactive and available:
        selected = select_option_dialog(
            title=msg(lang, en="Channel: Profile", ru="Канал: Профиль"),
            text=msg(
                lang,
                en="Select the profile that will own this channel. The profile provides the agent identity, runtime defaults, memory rules, and maximum permissions.",
                ru="Выберите профиль, к которому будет привязан этот канал. Профиль задаёт личность агента, runtime defaults, правила памяти и максимальные права.",
            ),
            options=list(available),
            default=(default if default in available else available[0]),
            hint_text=single_hint(lang),
        )
        return normalize_channel_choice_value(selected)
    return resolve_channel_text(
        value=profile_id,
        interactive=interactive,
        prompt_en="Profile id",
        prompt_ru="Идентификатор профиля",
        default=default,
        lang=lang,
        normalize_lower=True,
    )


def put_matching_binding(
    *,
    settings: Settings,
    binding_id: str,
    transport: str,
    profile_id: str,
    session_policy: SessionPolicy,
    priority: int,
    enabled: bool,
    account_id: str,
    prompt_overlay: str | None,
) -> None:
    """Create or update one matching binding for a channel endpoint."""

    run_channel_binding_service_sync(
        settings,
        lambda service: service.put(
            ChannelBindingRule(
                binding_id=binding_id,
                transport=transport,
                profile_id=profile_id,
                session_policy=session_policy,
                priority=priority,
                enabled=enabled,
                account_id=account_id,
                prompt_overlay=prompt_overlay,
            )
        ),
    )


def resolve_binding_update_inputs(
    *,
    settings: Settings,
    binding_id: str,
    session_policy: SessionPolicy | None,
    session_policy_default: SessionPolicy,
    priority: int | None,
    prompt_overlay: str | None,
) -> ResolvedBindingUpdateInputs:
    """Resolve binding update fields while preserving existing values when omitted."""

    existing: ChannelBindingRule | None = None
    try:
        existing = run_channel_binding_service_sync(
            settings,
            lambda service: service.get(binding_id=binding_id),
        )
    except ChannelBindingServiceError as exc:
        if exc.error_code != "channel_binding_not_found":
            raise
    return ResolvedBindingUpdateInputs(
        session_policy=(
            normalize_session_policy(session_policy)
            if session_policy is not None
            else (existing.session_policy if existing is not None else session_policy_default)
        ),
        priority=priority
        if priority is not None
        else (existing.priority if existing is not None else 0),
        prompt_overlay=prompt_overlay
        if prompt_overlay is not None
        else (existing.prompt_overlay if existing is not None else None),
    )


def build_ingress_batch_config(
    *,
    enabled: bool,
    debounce_ms: int,
    cooldown_sec: int,
    max_batch_size: int,
    max_buffer_chars: int,
) -> ChannelIngressBatchConfig:
    """Build one shared ingress-batching config from CLI flag values."""

    return ChannelIngressBatchConfig(
        enabled=enabled,
        debounce_ms=debounce_ms,
        cooldown_sec=cooldown_sec,
        max_batch_size=max_batch_size,
        max_buffer_chars=max_buffer_chars,
    )


def render_ingress_batch_summary(config: ChannelIngressBatchConfig) -> str:
    """Render one short operator-facing ingress batching summary."""

    if not config.enabled:
        return "off"
    cooldown = f",cooldown={config.cooldown_sec}s" if config.cooldown_sec > 0 else ""
    return (
        f"on(delay={config.debounce_ms}ms{cooldown},"
        f"size={config.max_batch_size},chars={config.max_buffer_chars})"
    )


def build_reply_humanization_config(
    *,
    enabled: bool,
    min_delay_ms: int,
    max_delay_ms: int,
    chars_per_second: int,
) -> ChannelReplyHumanizationConfig:
    """Build one shared reply-humanization config from CLI flag values."""

    return ChannelReplyHumanizationConfig(
        enabled=enabled,
        min_delay_ms=min_delay_ms,
        max_delay_ms=max_delay_ms,
        chars_per_second=chars_per_second,
    )


def merge_ingress_batch_config(
    *,
    current: ChannelIngressBatchConfig,
    enabled: bool | None = None,
    debounce_ms: int | None = None,
    cooldown_sec: int | None = None,
    max_batch_size: int | None = None,
    max_buffer_chars: int | None = None,
) -> ChannelIngressBatchConfig:
    """Merge optional CLI overrides into one ingress-batch config."""

    return ChannelIngressBatchConfig(
        enabled=current.enabled if enabled is None else enabled,
        debounce_ms=current.debounce_ms if debounce_ms is None else debounce_ms,
        cooldown_sec=current.cooldown_sec if cooldown_sec is None else cooldown_sec,
        max_batch_size=current.max_batch_size if max_batch_size is None else max_batch_size,
        max_buffer_chars=current.max_buffer_chars if max_buffer_chars is None else max_buffer_chars,
    )


def merge_reply_humanization_config(
    *,
    current: ChannelReplyHumanizationConfig,
    enabled: bool | None = None,
    min_delay_ms: int | None = None,
    max_delay_ms: int | None = None,
    chars_per_second: int | None = None,
) -> ChannelReplyHumanizationConfig:
    """Merge optional CLI overrides into one reply-humanization config."""

    return ChannelReplyHumanizationConfig(
        enabled=current.enabled if enabled is None else enabled,
        min_delay_ms=current.min_delay_ms if min_delay_ms is None else min_delay_ms,
        max_delay_ms=current.max_delay_ms if max_delay_ms is None else max_delay_ms,
        chars_per_second=current.chars_per_second if chars_per_second is None else chars_per_second,
    )


def render_reply_humanization_summary(config: ChannelReplyHumanizationConfig) -> str:
    """Render one short operator-facing reply-humanization summary."""

    if not config.enabled:
        return "off"
    return (
        f"on(delay={config.min_delay_ms}-{config.max_delay_ms}ms,"
        f"speed={config.chars_per_second}cps)"
    )


def normalize_channel_choice_value(value: str) -> str:
    """Normalize one persisted CLI choice value to canonical lowercase form."""

    return value.strip().lower()


def resolve_channel_update_profile_id(*, profile_id: str | None, current_profile_id: str) -> str:
    """Resolve one channel update profile id using the current value when no override was passed."""

    return current_profile_id if profile_id is None else normalize_channel_choice_value(profile_id)


def normalize_channel_tool_profile(value: str) -> ChannelToolProfile:
    """Normalize one channel tool-profile choice to its literal type."""

    return cast(ChannelToolProfile, normalize_channel_choice_value(value))


def normalize_session_policy(value: str) -> SessionPolicy:
    """Normalize one binding session-policy choice to its literal type."""

    return cast(SessionPolicy, normalize_channel_choice_value(value))
