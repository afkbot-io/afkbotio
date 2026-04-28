"""Interactive prompts for plugin installation and related setup choices."""

from __future__ import annotations

from urllib.parse import urlparse

import typer

from afkbot.cli.presentation.inline_select import confirm_space, run_inline_single_select
from afkbot.cli.presentation.prompt_i18n import (
    PromptLanguage,
    msg,
    no_label,
    resolve_prompt_language,
    single_hint,
    yes_label,
)
from afkbot.services.plugins.catalog import list_uninstalled_known_plugins
from afkbot.settings import Settings

_CUSTOM_PLUGIN_SOURCE = "__custom_plugin_source__"


def prompt_plugin_install_source(
    *,
    settings: Settings,
    installed_plugin_ids: set[str] | frozenset[str],
    installed_plugin_labels: tuple[str, ...] = (),
) -> str | None:
    """Prompt for one install source from the curated catalog or a custom source."""

    available_plugins = list_uninstalled_known_plugins(installed_plugin_ids=installed_plugin_ids)
    lang = resolve_prompt_language(settings=settings, value=None, ru=False)
    options = [
        (
            plugin.plugin_id,
            f"{plugin.name} - {plugin.description}",
        )
        for plugin in available_plugins
    ]
    options.append(
        (
            _CUSTOM_PLUGIN_SOURCE,
            msg(
                lang,
                en="Custom GitHub URL or github:owner/repo@ref",
                ru="Свой GitHub URL или github:owner/repo@ref",
            ),
        )
    )
    default_value = options[0][0]
    selected = run_inline_single_select(
        title=msg(lang, en="Plugins: Install", ru="Плагины: Установка"),
        text=_plugin_install_prompt_text(
            lang=lang,
            installed_plugin_labels=installed_plugin_labels,
        ),
        options=options,
        default_value=default_value,
        hint_text=single_hint(lang),
    )
    if selected is None:
        return None
    if selected != _CUSTOM_PLUGIN_SOURCE:
        for plugin in available_plugins:
            if plugin.plugin_id == selected:
                return plugin.source_ref
        return None
    return _prompt_custom_plugin_source(lang=lang)


def prompt_update_notices_enabled(
    *,
    default: bool,
    lang: PromptLanguage = PromptLanguage.EN,
) -> bool:
    """Prompt whether `afk chat` should ask about available AFKBOT updates."""

    return confirm_space(
        question=msg(
            lang,
            en=(
                "When `afk chat` starts and AFKBOT sees a newer version, ask whether to install it before "
                "opening the chat?"
            ),
            ru=(
                "Когда `afk chat` стартует и AFKBOT видит новую версию, спрашивать, установить ли её "
                "перед открытием чата?"
            ),
        ),
        default=default,
        title=msg(lang, en="Setup: Update prompts", ru="Настройка: Подсказки об обновлениях"),
        yes_label=yes_label(lang),
        no_label=no_label(lang),
        hint_text=single_hint(lang),
    )


def _prompt_custom_plugin_source(*, lang: PromptLanguage) -> str:
    while True:
        value = str(
            typer.prompt(
                msg(
                    lang,
                    en="Plugin source",
                    ru="Источник плагина",
                )
            )
        ).strip()
        if _is_supported_custom_plugin_source(value):
            return value
        typer.echo(
            msg(
                lang,
                en="Enter a GitHub URL or `github:owner/repo@ref`.",
                ru="Укажите ссылку GitHub или `github:owner/repo@ref`.",
            )
        )


def _plugin_install_prompt_text(
    *,
    lang: PromptLanguage,
    installed_plugin_labels: tuple[str, ...],
) -> str:
    installed_count = len(installed_plugin_labels)
    installed_summary = ", ".join(installed_plugin_labels)
    return msg(
        lang,
        en=(
            f"Installed plugins: {installed_count} ({installed_summary or 'none'}).\n\n"
            "Choose one plugin to install. The list shows known plugins that are not "
            "installed yet, plus a custom GitHub source option. Plugins can add skills, commands, "
            "or integrations to the local profile environment."
        ),
        ru=(
            f"Уже установлено плагинов: {installed_count} ({installed_summary or 'нет'}).\n\n"
            "Выберите плагин для установки. В списке показаны известные плагины, "
            "которые ещё не установлены, и отдельный пункт для собственного GitHub-источника. "
            "Плагины могут добавлять навыки, команды или интеграции в локальное окружение профиля."
        ),
    )


def _is_supported_custom_plugin_source(value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return False
    if normalized.startswith("github:"):
        owner_repo = normalized.removeprefix("github:").strip().partition("@")[0]
        owner, slash, repo = owner_repo.partition("/")
        return bool(owner and slash and repo)

    parsed = urlparse(normalized.partition("@")[0])
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.netloc not in {"github.com", "www.github.com"}:
        return False
    parts = [item for item in parsed.path.strip("/").split("/") if item]
    return len(parts) >= 2


__all__ = [
    "_plugin_install_prompt_text",
    "prompt_plugin_install_source",
    "prompt_update_notices_enabled",
]
