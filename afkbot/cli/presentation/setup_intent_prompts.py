"""Intent-first profile/setup prompt helpers."""

from __future__ import annotations

import typer

from afkbot.cli.presentation.inline_select import select_multi_option_dialog
from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg, multi_hint
from afkbot.cli.presentation.setup_policy_prompts import select_value_dialog
from afkbot.services.wizard.profile_intent_catalog import (
    list_profile_intent_confirmations,
    list_profile_intent_depths,
    list_profile_intent_isolations,
    list_profile_intent_networks,
    list_profile_intent_work_contexts,
    profile_intent_action_choices_for_contexts,
    profile_intent_default_actions,
)


def prompt_profile_intent_depth(
    *,
    default: str = "guided",
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt how detailed the setup/profile security flow should be."""

    choices = list_profile_intent_depths()
    values = {choice.id for choice in choices}
    return select_value_dialog(
        title=msg(lang, en="Setup: Security setup", ru="Настройка: Безопасность"),
        text=msg(
            lang,
            en=(
                "Choose how to configure this profile. You can change these settings later with "
                "`afk setup` or `afk profile update`."
            ),
            ru=(
                "Выберите, как настроить этот профиль. Всё можно изменить позже через "
                "`afk setup` или `afk profile update`."
            ),
        ),
        options=[(choice.id, choice.label(lang=lang)) for choice in choices],
        default=default if default in values else "guided",
        lang=lang,
    )


def prompt_profile_intent_work_contexts(
    *,
    default_values: tuple[str, ...] = ("channels",),
    include_expert: bool = False,
    lang: PromptLanguage = PromptLanguage.EN,
) -> tuple[str, ...]:
    """Prompt where the bot should be allowed to work."""

    choices = list_profile_intent_work_contexts(include_expert=include_expert)
    return select_multi_option_dialog(
        title=msg(lang, en="Setup: Where will the bot work?", ru="Настройка: Где будет работать бот?"),
        text=msg(
            lang,
            en="Choose one or more surfaces. AFKBOT will ask only the follow-up questions that matter.",
            ru="Выберите один или несколько вариантов. AFKBOT задаст только нужные уточняющие вопросы.",
        ),
        options=[(choice.id, choice.label(lang=lang)) for choice in choices],
        default_values=tuple(value for value in default_values if value in {choice.id for choice in choices}),
        hint_text=multi_hint(lang),
    )


def prompt_profile_intent_actions(
    *,
    work_contexts: tuple[str, ...],
    default_values: tuple[str, ...] | None = None,
    include_expert: bool = False,
    lang: PromptLanguage = PromptLanguage.EN,
) -> tuple[str, ...]:
    """Prompt what the bot may do in the selected surfaces."""

    choices = profile_intent_action_choices_for_contexts(
        work_contexts,
        include_expert=include_expert,
    )
    defaults = default_values if default_values is not None else profile_intent_default_actions(work_contexts)
    return select_multi_option_dialog(
        title=msg(lang, en="Setup: What may the bot do?", ru="Настройка: Что боту можно делать?"),
        text=msg(
            lang,
            en=(
                "Choose product capabilities in plain language. Channel-owned actions remain scoped to the "
                "active channel endpoint and do not grant general filesystem or terminal access."
            ),
            ru=(
                "Выберите возможности обычным языком. Действия канала остаются ограничены активным подключением "
                "и не дают общий доступ к файлам или терминалу."
            ),
        ),
        options=[(choice.id, choice.label(lang=lang)) for choice in choices],
        default_values=tuple(value for value in defaults if value in {choice.id for choice in choices}),
        hint_text=multi_hint(lang),
    )


def prompt_profile_intent_isolation(
    *,
    default: str = "no_files",
    include_dangerous: bool = False,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt file and terminal isolation for the profile."""

    choices = list_profile_intent_isolations(include_dangerous=include_dangerous)
    values = {choice.id for choice in choices}
    return select_value_dialog(
        title=msg(lang, en="Setup: Isolation", ru="Настройка: Изоляция"),
        text=msg(
            lang,
            en="Choose the file and terminal boundary. The profile policy enforces this boundary at runtime.",
            ru="Выберите границу файлов и терминала. Политика профиля применяет эту границу при работе.",
        ),
        options=[(choice.id, choice.label(lang=lang)) for choice in choices],
        default=default if default in values else "no_files",
        lang=lang,
    )


def prompt_profile_intent_confirmation(
    *,
    default: str = "balanced",
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt how cautious the runtime should be before risky actions."""

    choices = list_profile_intent_confirmations()
    values = {choice.id for choice in choices}
    return select_value_dialog(
        title=msg(lang, en="Setup: Confirmations", ru="Настройка: Подтверждения"),
        text=msg(
            lang,
            en="Choose how often AFKBOT should ask before risky actions.",
            ru="Выберите, как часто AFKBOT должен спрашивать перед рискованными действиями.",
        ),
        options=[(choice.id, choice.label(lang=lang)) for choice in choices],
        default=default if default in values else "balanced",
        lang=lang,
    )


def prompt_profile_intent_network(
    *,
    default: str = "recommended",
    include_dangerous: bool = False,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt network boundary for web/HTTP actions."""

    choices = list_profile_intent_networks(include_dangerous=include_dangerous)
    values = {choice.id for choice in choices}
    return select_value_dialog(
        title=msg(lang, en="Setup: Network", ru="Настройка: Сеть"),
        text=msg(
            lang,
            en="Choose which network destinations are allowed for web and integration tools.",
            ru="Выберите, какие сетевые направления разрешены для веб-страниц и интеграционных инструментов.",
        ),
        options=[(choice.id, choice.label(lang=lang)) for choice in choices],
        default=default if default in values else "recommended",
        lang=lang,
    )


def prompt_profile_intent_network_allowlist(
    *,
    default_values: tuple[str, ...] = (),
    lang: PromptLanguage = PromptLanguage.EN,
) -> tuple[str, ...]:
    """Prompt custom network domains for guided intent setup."""

    rendered_default = ",".join(default_values)
    value = str(
        typer.prompt(
            msg(
                lang,
                en="Allowed network domains (comma-separated, for example api.example.com)",
                ru="Разрешённые сетевые домены (через запятую, например api.example.com)",
            ),
            default=rendered_default,
        )
    ).strip()
    return tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
