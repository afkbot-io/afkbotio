"""Language helpers and shared copy for interactive CLI prompts."""

from __future__ import annotations

from enum import StrEnum
import locale
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from afkbot.settings import Settings


class PromptLanguage(StrEnum):
    """Supported interactive prompt languages."""

    EN = "en"
    RU = "ru"


def detect_system_locale() -> str | None:
    """Return the best-effort current system locale string."""

    for env_name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        raw_value = str(os.getenv(env_name) or "").strip()
        if raw_value:
            return raw_value
    try:
        language_code, encoding = locale.getlocale()
    except ValueError:
        language_code, encoding = (None, None)
    parts = [part for part in (language_code, encoding) if part]
    if parts:
        return ".".join(parts)
    return None


def detect_system_prompt_language() -> PromptLanguage:
    """Return the preferred CLI language inferred from the local system locale."""

    locale_name = str(detect_system_locale() or "").strip().lower().replace("-", "_")
    if locale_name.startswith("ru"):
        return PromptLanguage.RU
    return PromptLanguage.EN


def normalize_prompt_language(*, value: str | None, ru: bool) -> PromptLanguage:
    """Normalize `--lang`/`--ru` flags to one supported interactive language."""

    normalized = str(value or "").strip().lower()
    if ru:
        if normalized and normalized not in {"ru", "russian", "ru-ru"}:
            raise ValueError("--ru cannot be combined with --lang values other than 'ru'")
        return PromptLanguage.RU
    if not normalized:
        return detect_system_prompt_language()
    if normalized in {"en", "english", "en-us", "en-gb"}:
        return PromptLanguage.EN
    if normalized in {"ru", "russian", "ru-ru"}:
        return PromptLanguage.RU
    raise ValueError("prompt language must be one of: en, ru")


def resolve_prompt_language(*, settings: Settings | None, value: str | None, ru: bool) -> PromptLanguage:
    """Resolve prompt language from explicit flags, persisted project config, then system locale."""

    normalized = str(value or "").strip()
    if ru or normalized:
        return normalize_prompt_language(value=value, ru=ru)
    if settings is not None:
        from afkbot.services.setup.runtime_store import read_runtime_config

        configured = str(read_runtime_config(settings).get("prompt_language") or "").strip()
        if configured:
            try:
                return normalize_prompt_language(value=configured, ru=False)
            except ValueError:
                pass
    return detect_system_prompt_language()


def msg(lang: PromptLanguage, *, en: str, ru: str) -> str:
    """Return one localized message string for interactive CLI flows."""

    return ru if lang == PromptLanguage.RU else en


def yes_label(lang: PromptLanguage) -> str:
    """Return localized yes label."""

    return "Да" if lang == PromptLanguage.RU else "Yes"


def no_label(lang: PromptLanguage) -> str:
    """Return localized no label."""

    return "Нет" if lang == PromptLanguage.RU else "No"


def single_hint(lang: PromptLanguage) -> str:
    """Return localized inline single-select hint."""

    return (
        "↑/↓ перемещение, Enter подтвердить, Esc отмена"
        if lang == PromptLanguage.RU
        else "↑/↓ move, Enter confirm, Esc cancel"
    )


def multi_hint(lang: PromptLanguage) -> str:
    """Return localized inline multi-select hint."""

    return (
        "↑/↓ перемещение, Space переключить, A всё/ничего, Enter подтвердить, Esc отмена"
        if lang == PromptLanguage.RU
        else "↑/↓ move, Space toggle, A all/none, Enter confirm, Esc cancel"
    )
