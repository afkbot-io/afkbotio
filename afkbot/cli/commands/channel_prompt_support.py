"""Shared interactive prompt helpers for channel add/update flows."""

from __future__ import annotations

from collections.abc import Mapping

import typer

from afkbot.cli.presentation.inline_select import confirm_space, run_inline_single_select
from afkbot.cli.presentation.setup_prompts import PromptLanguage, msg
from afkbot.cli.presentation.prompt_i18n import no_label, single_hint, yes_label


def resolve_channel_text(
    *,
    value: str | None,
    interactive: bool,
    prompt_en: str,
    prompt_ru: str,
    default: str | None,
    lang: PromptLanguage,
    allow_empty: bool = False,
    normalize_lower: bool = False,
    detail_en: str | None = None,
    detail_ru: str | None = None,
) -> str:
    """Resolve one channel text field from explicit flag or interactive prompt."""

    if value is not None:
        resolved = value.strip()
    elif interactive:
        if detail_en or detail_ru:
            typer.echo(msg(lang, en=detail_en or "", ru=detail_ru or ""))
        if default is None:
            resolved = typer.prompt(msg(lang, en=prompt_en, ru=prompt_ru)).strip()
        else:
            resolved = typer.prompt(
                msg(lang, en=prompt_en, ru=prompt_ru),
                default=default,
            ).strip()
    else:
        if default is None:
            raise typer.BadParameter(
                msg(
                    lang,
                    en=f"{prompt_en} is required",
                    ru=f"{prompt_ru} обязателен",
                )
            )
        resolved = str(default).strip()
    if normalize_lower:
        resolved = resolved.lower()
    if not allow_empty and not resolved:
        raise typer.BadParameter(
            msg(
                lang,
                en=f"{prompt_en} cannot be empty",
                ru=f"{prompt_ru} не может быть пустым",
            )
        )
    return resolved


def resolve_channel_choice(
    *,
    value: str | None,
    interactive: bool,
    prompt_en: str,
    prompt_ru: str,
    default: str,
    allowed: tuple[str, ...],
    lang: PromptLanguage,
    detail_en: str | None = None,
    detail_ru: str | None = None,
    label_overrides: Mapping[str, tuple[str, str]] | None = None,
) -> str:
    """Resolve one normalized channel choice field."""

    if value is not None:
        resolved = value.strip().lower()
    elif interactive:
        resolved = _select_channel_option(
            title=msg(lang, en=prompt_en, ru=prompt_ru),
            text=msg(
                lang,
                en=detail_en or f"Select {prompt_en.lower()}.",
                ru=detail_ru or f"Выберите значение для «{prompt_ru}».",
            ),
            allowed=allowed,
            default=default,
            lang=lang,
            label_overrides=label_overrides,
        ).strip().lower()
    else:
        resolved = default.strip().lower()
    if resolved not in allowed:
        allowed_text = ", ".join(allowed)
        raise typer.BadParameter(
            msg(
                lang,
                en=f"{prompt_en} must be one of: {allowed_text}",
                ru=f"{prompt_ru} должен быть одним из: {allowed_text}",
            )
    )
    return resolved


def _select_channel_option(
    *,
    title: str,
    text: str,
    allowed: tuple[str, ...],
    default: str,
    lang: PromptLanguage,
    label_overrides: Mapping[str, tuple[str, str]] | None = None,
) -> str:
    """Render a channel choice with human labels while returning the stable config value."""

    selected = run_inline_single_select(
        title=title,
        text=text,
        options=[
            (
                item,
                _channel_choice_label(item, lang=lang, label_overrides=label_overrides),
            )
            for item in allowed
        ],
        default_value=default,
        hint_text=single_hint(lang),
    )
    return str(selected).strip() if selected else default


def _channel_choice_label(
    value: str,
    *,
    lang: PromptLanguage,
    label_overrides: Mapping[str, tuple[str, str]] | None = None,
) -> str:
    """Return beginner-friendly labels for stable channel config values."""

    if label_overrides is not None and value in label_overrides:
        override = label_overrides[value]
        return override[1] if lang == PromptLanguage.RU else override[0]

    labels: dict[str, tuple[str, str]] = {
        "inherit": (
            "inherit - use the profile's full tool ceiling for this channel",
            "inherit - использовать полный потолок прав профиля в этом канале",
        ),
        "chat_minimal": (
            "chat_minimal - replies only, no tools exposed to the channel",
            "chat_minimal - только ответы, без инструментов в канале",
        ),
        "messaging_safe": (
            "messaging_safe - replies, channel.send, and safe memory tools",
            "messaging_safe - ответы, channel.send и безопасная работа с памятью",
        ),
        "support_readonly": (
            "support_readonly - messaging_safe plus read-only file search/read",
            "support_readonly - messaging_safe плюс чтение и поиск по файлам",
        ),
        "main": (
            "main - one shared conversation for the whole binding",
            "main - одна общая беседа для всей привязки",
        ),
        "per-chat": (
            "per-chat - separate conversation for each Telegram chat",
            "per-chat - отдельная беседа для каждого Telegram-чата",
        ),
        "per-thread": (
            "per-thread - separate conversation for each group topic/thread",
            "per-thread - отдельная беседа для каждой темы или треда группы",
        ),
        "per-user-in-group": (
            "per-user-in-group - separate conversation per user inside groups",
            "per-user-in-group - отдельная беседа для каждого пользователя в группе",
        ),
        "open": (
            "open - allow this chat type unless another rule blocks it",
            "open - разрешить этот тип чата, если другая проверка не блокирует",
        ),
        "allowlist": (
            "allowlist - allow only the IDs you enter in the next question",
            "allowlist - разрешить только ID, которые вы введёте дальше",
        ),
        "disabled": (
            "disabled - reject this chat type completely",
            "disabled - полностью запретить этот тип чата",
        ),
        "mention_or_reply": (
            "mention_or_reply - trigger in groups on bot mentions or replies",
            "mention_or_reply - в группах запускать по упоминанию бота или ответу на него",
        ),
        "reply_only": (
            "reply_only - trigger only when someone replies to the bot/user account",
            "reply_only - запускать только когда отвечают боту или user-аккаунту",
        ),
        "mention_only": (
            "mention_only - trigger in groups only on bot mentions",
            "mention_only - в группах запускать только по упоминанию бота",
        ),
        "all_messages": (
            "all_messages - process every allowed group message",
            "all_messages - обрабатывать каждое разрешённое сообщение в группе",
        ),
        "same_chat": (
            "same_chat - send replies back to the same Telegram chat",
            "same_chat - отправлять ответы обратно в тот же Telegram-чат",
        ),
        "reply_or_command": (
            "reply_or_command - trigger on replies to the account or command prefix",
            "reply_or_command - запускать по ответу аккаунту или по командному префиксу",
        ),
        "command_only": (
            "command_only - trigger only on command-prefix messages",
            "command_only - запускать только по сообщениям с командным префиксом",
        ),
    }
    label = labels.get(value)
    if label is None:
        return value
    return label[1] if lang == PromptLanguage.RU else label[0]


def resolve_channel_bool(
    *,
    value: bool | None,
    interactive: bool,
    prompt_en: str,
    prompt_ru: str,
    default: bool,
    lang: PromptLanguage,
    detail_en: str | None = None,
    detail_ru: str | None = None,
) -> bool:
    """Resolve one channel boolean field."""

    if value is not None:
        return value
    if interactive:
        return bool(
            confirm_space(
                question=msg(lang, en=detail_en or prompt_en, ru=detail_ru or prompt_ru),
                default=default,
                title=msg(lang, en=prompt_en, ru=prompt_ru),
                yes_label=yes_label(lang),
                no_label=no_label(lang),
                hint_text=single_hint(lang),
            )
        )
    return default


def resolve_channel_int(
    *,
    value: int | None,
    interactive: bool,
    prompt_en: str,
    prompt_ru: str,
    default: int,
    lang: PromptLanguage,
    min_value: int | None = None,
    max_value: int | None = None,
    detail_en: str | None = None,
    detail_ru: str | None = None,
) -> int:
    """Resolve one channel integer field with optional bounds."""

    if value is not None:
        resolved = value
    elif interactive:
        if detail_en or detail_ru:
            typer.echo(msg(lang, en=detail_en or "", ru=detail_ru or ""))
        resolved = int(
            typer.prompt(
                msg(lang, en=prompt_en, ru=prompt_ru),
                default=default,
                type=int,
            )
        )
    else:
        resolved = int(default)
    if min_value is not None and resolved < min_value:
        raise typer.BadParameter(
            msg(
                lang,
                en=f"{prompt_en} must be >= {min_value}",
                ru=f"{prompt_ru} должен быть >= {min_value}",
            )
        )
    if max_value is not None and resolved > max_value:
        raise typer.BadParameter(
            msg(
                lang,
                en=f"{prompt_en} must be <= {max_value}",
                ru=f"{prompt_ru} должен быть <= {max_value}",
            )
        )
    return resolved


def resolve_channel_secret(
    *,
    value: str | None,
    interactive: bool,
    prompt_en: str,
    prompt_ru: str,
    lang: PromptLanguage,
    existing_configured: bool,
    required: bool,
    detail_en: str | None = None,
    detail_ru: str | None = None,
) -> str | None:
    """Resolve one secret value from explicit input or hidden prompt with keep-current semantics."""

    if value is not None:
        normalized = value.strip()
    elif interactive:
        if detail_en or detail_ru:
            typer.echo(msg(lang, en=detail_en or "", ru=detail_ru or ""))
        if existing_configured:
            typer.echo(
                msg(
                    lang,
                    en=f"{prompt_en} is currently configured. Press Enter to keep it, or type a new value.",
                    ru=f"{prompt_ru} уже настроен. Нажмите Enter, чтобы оставить его, или введите новое значение.",
                )
            )
        normalized = str(
            typer.prompt(
                msg(lang, en=prompt_en, ru=prompt_ru),
                default="",
                show_default=False,
                hide_input=True,
            )
        ).strip()
    else:
        normalized = ""
    if not normalized:
        if existing_configured:
            return None
        if required:
            raise typer.BadParameter(
                msg(
                    lang,
                    en=f"{prompt_en} cannot be empty",
                    ru=f"{prompt_ru} не может быть пустым",
                )
            )
        return None
    return normalized
