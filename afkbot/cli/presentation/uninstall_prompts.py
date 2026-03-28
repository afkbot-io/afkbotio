"""Interactive uninstall confirmation prompt."""

from __future__ import annotations

import os

import typer

from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg


def prompt_uninstall_confirmation() -> bool:
    """Confirm one full AFKBOT uninstall in the active interactive language."""

    lang = _resolve_language()
    return bool(
        typer.confirm(
            msg(
                lang,
                en=(
                    "Remove AFKBOT local services, data volumes, runtime config, "
                    "secrets, and profiles workspace?"
                ),
                ru=(
                    "Удалить локальные сервисы AFKBOT, тома данных, runtime config, "
                    "secrets и workspace профилей?"
                ),
            ),
            default=False,
        )
    )


def _resolve_language() -> PromptLanguage:
    if str(os.getenv("AFKBOT_PROMPT_LANGUAGE") or "").strip().lower() == "ru":
        return PromptLanguage.RU
    return PromptLanguage.EN
