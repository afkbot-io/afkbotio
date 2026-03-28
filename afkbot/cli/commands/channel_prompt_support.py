"""Shared interactive prompt helpers for channel add/update flows."""

from __future__ import annotations

import typer

from afkbot.cli.presentation.inline_select import confirm_space, select_option_dialog
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
) -> str:
    """Resolve one normalized channel choice field."""

    if value is not None:
        resolved = value.strip().lower()
    elif interactive:
        resolved = select_option_dialog(
            title=msg(lang, en=prompt_en, ru=prompt_ru),
            text=msg(
                lang,
                en=detail_en or f"Select {prompt_en.lower()}.",
                ru=detail_ru or f"Выберите значение для «{prompt_ru}».",
            ),
            options=list(allowed),
            default=default,
            hint_text=single_hint(lang),
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
