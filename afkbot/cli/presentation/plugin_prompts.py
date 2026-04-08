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
                ru="Своя ссылка GitHub или github:owner/repo@ref",
            ),
        )
    )
    default_value = options[0][0]
    selected = run_inline_single_select(
        title=msg(lang, en="Plugins: Install", ru="Плагины: Установка"),
        text=msg(
            lang,
            en=(
                "Choose one plugin to install. The list shows known plugins that are not "
                "installed yet, plus a custom GitHub source option."
            ),
            ru=(
                "Выберите плагин для установки. В списке показаны известные плагины, "
                "которые ещё не установлены, и отдельный пункт для собственного GitHub-источника."
            ),
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
            en="Prompt to install AFKBOT updates when `afk chat` starts and a new update is available?",
            ru="Предлагать установить обновление AFKBOT при старте `afk chat`, когда доступна новая версия?",
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
    "prompt_plugin_install_source",
    "prompt_update_notices_enabled",
]
