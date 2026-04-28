"""Interactive prompts for browser backend selection and CDP configuration."""

from __future__ import annotations

import sys
from typing import cast

import typer

from afkbot.browser_backends import (
    BrowserBackendId,
    LIGHTPANDA_DEFAULT_CDP_URL,
    browser_backend_choices,
    get_browser_backend_spec,
)
from afkbot.cli.presentation.inline_select import run_inline_single_select
from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg, single_hint


def prompt_browser_backend(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> BrowserBackendId:
    """Prompt the browser backend via inline selector with manual fallback."""

    choices = browser_backend_choices()
    default_backend = default if default in choices else choices[0]
    selected = run_inline_single_select(
        title=msg(lang, en="Browser: Runtime backend", ru="Браузер: Способ запуска"),
        text=msg(
            lang,
            en=(
                "Select how `browser.control` will drive a browser. Use local Chromium for easiest setup; "
                "use CDP when a server or container already exposes a browser endpoint."
            ),
            ru=(
                "Выберите, как `browser.control` будет управлять браузером. Локальный Chromium проще для "
                "локальной настройки; CDP подходит, если сервер или контейнер уже предоставляет адрес подключения."
            ),
        ),
        options=[
            (
                spec.id,
                f"{spec.label} - {_backend_summary(spec.id, lang=lang)}",
            )
            for spec in (get_browser_backend_spec(item) for item in choices)
        ],
        default_value=default_backend,
        hint_text=single_hint(lang),
    )
    if selected in choices:
        return cast(BrowserBackendId, selected)

    options = "/".join(choices)
    while True:
        value = str(
            typer.prompt(
                msg(
                    lang,
                    en=f"Browser backend ({options})",
                    ru=f"Способ запуска браузера ({options})",
                ),
                default=default_backend,
            )
        ).strip().lower()
        if value in choices:
            return cast(BrowserBackendId, value)
        typer.echo(
            msg(
                lang,
                en=f"Invalid browser backend: choose one of {options}.",
                ru=f"Некорректный способ запуска браузера: выберите один из {options}.",
            )
        )


def prompt_browser_cdp_url(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt one CDP endpoint URL for external browser backends."""

    suggested = default.strip() or LIGHTPANDA_DEFAULT_CDP_URL
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return suggested
    while True:
        value = str(
            typer.prompt(
                msg(lang, en="Browser CDP URL", ru="URL подключения к браузеру (CDP)"),
                default=suggested,
            )
        ).strip()
        if value:
            return value
        typer.echo(
            msg(
                lang,
                en="Browser CDP URL cannot be empty.",
                ru="URL подключения к браузеру (CDP) не может быть пустым.",
            )
        )


def _backend_summary(backend: BrowserBackendId, *, lang: PromptLanguage) -> str:
    """Return localized backend summary for wizard options."""

    if backend == "lightpanda_cdp":
        return msg(
            lang,
            en="best for headless servers, connects to an external CDP browser",
            ru="лучше для серверов без GUI, подключается к внешнему CDP-браузеру",
        )
    return msg(
        lang,
        en="best compatibility, installs and launches local Chromium",
        ru="лучшая совместимость, устанавливает и запускает локальный Chromium",
    )
