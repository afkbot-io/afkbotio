"""Interactive prompts for chat-time update notices."""

from __future__ import annotations

from enum import StrEnum

from afkbot.cli.presentation.inline_select import run_inline_single_select
from afkbot.cli.presentation.prompt_i18n import PromptLanguage, msg, single_hint
from afkbot.services.update_runtime import UpdateAvailability


class ChatUpdateAction(StrEnum):
    """Supported operator decisions for a pending update."""

    INSTALL = "install"
    SKIP = "skip"
    REMIND_WEEK = "remind_week"


def prompt_chat_update_action(
    *,
    availability: UpdateAvailability,
    lang: PromptLanguage,
) -> ChatUpdateAction | None:
    """Ask whether to install or snooze one pending AFKBOT update."""

    selected = run_inline_single_select(
        title=msg(
            lang,
            en="AFKBOT Update Available",
            ru="Доступно обновление AFKBOT",
        ),
        text=_build_update_prompt_text(availability=availability, lang=lang),
        options=[
            (
                ChatUpdateAction.INSTALL.value,
                msg(lang, en="Yes", ru="Да"),
            ),
            (
                ChatUpdateAction.SKIP.value,
                msg(lang, en="No", ru="Нет"),
            ),
            (
                ChatUpdateAction.REMIND_WEEK.value,
                msg(lang, en="Remind in a week", ru="Напомнить через неделю"),
            ),
        ],
        default_value=ChatUpdateAction.SKIP.value,
        hint_text=single_hint(lang),
    )
    if selected is None:
        return None
    return ChatUpdateAction(str(selected).strip() or ChatUpdateAction.SKIP.value)


def _build_update_prompt_text(*, availability: UpdateAvailability, lang: PromptLanguage) -> str:
    if availability.target_id.startswith("package:"):
        return msg(
            lang,
            en=(
                f"{availability.target_label} is available. "
                f"Current install: {availability.current_version}. Install it before chat starts?"
            ),
            ru=(
                f"Доступно обновление {availability.target_label}. "
                f"Текущая установка: {availability.current_version}. Установить его перед стартом чата?"
            ),
        )
    return msg(
        lang,
        en=(
            f"A newer update is available: {availability.target_label}. "
            f"Current install: {availability.current_version}. Install it before chat starts?"
        ),
        ru=(
            f"Доступно более новое обновление: {availability.target_label}. "
            f"Текущая установка: {availability.current_version}. Установить его перед стартом чата?"
        ),
    )


__all__ = ["ChatUpdateAction", "prompt_chat_update_action"]
