"""Provider- and runtime-specific prompts for interactive setup flow."""

from __future__ import annotations

from typing import Final

import typer

from afkbot.cli.presentation.inline_select import confirm_space, select_option_dialog
from afkbot.cli.presentation.prompt_i18n import (
    PromptLanguage,
    msg,
    no_label,
    single_hint,
    yes_label,
)
from afkbot.services.llm.provider_catalog import (
    get_provider_spec,
    parse_provider,
    provider_choices,
)

LLM_PROVIDER_CHOICES: Final[tuple[str, ...]] = provider_choices(include_none=False)
THINKING_LEVEL_CHOICES: Final[tuple[str, ...]] = ("low", "medium", "high", "very_high")
HTTP_PROXY_TYPE: Final[str] = "http"
SOCKS5_PROXY_TYPE: Final[str] = "socks5"
SOCKS5H_PROXY_TYPE: Final[str] = "socks5h"
PROXY_TYPE_CHOICES: Final[tuple[str, ...]] = (
    "none",
    HTTP_PROXY_TYPE,
    SOCKS5_PROXY_TYPE,
    SOCKS5H_PROXY_TYPE,
)


def prompt_provider(*, default: str, lang: PromptLanguage = PromptLanguage.EN) -> str:
    """Prompt LLM provider via inline selector with robust fallback."""

    selected_default = default if default in LLM_PROVIDER_CHOICES else LLM_PROVIDER_CHOICES[0]
    selected = select_option_dialog(
        title=msg(lang, en="Setup: LLM provider", ru="Настройка: LLM провайдер"),
        text=msg(
            lang,
            en="Select the default provider for chat runtime.",
            ru="Выберите провайдера по умолчанию для чата.",
        ),
        options=list(LLM_PROVIDER_CHOICES),
        default=selected_default,
        hint_text=single_hint(lang),
    )
    if selected in LLM_PROVIDER_CHOICES:
        return selected
    options = "/".join(LLM_PROVIDER_CHOICES)
    while True:
        provider = str(
            typer.prompt(
                msg(
                    lang,
                    en=f"LLM provider ({options})",
                    ru=f"LLM провайдер ({options})",
                ),
                default=selected_default,
            )
        ).strip().lower()
        if provider in LLM_PROVIDER_CHOICES:
            return provider
        typer.echo(
            msg(
                lang,
                en=f"Invalid provider: choose one of {options}.",
                ru=f"Некорректный провайдер: выберите один из {options}.",
            )
        )


def prompt_chat_model(
    *,
    provider: str,
    default_model: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt chat model by provider with preset choices and manual fallback."""

    provider_id = parse_provider(provider)
    spec = get_provider_spec(provider_id)
    preset_options = list(spec.model_choices)
    if provider_id.value == "custom":
        options = ["custom", "manual"]
    else:
        options = [*preset_options, "manual"] if preset_options else ["manual"]
    selected_default = default_model if default_model in preset_options else options[0]
    selected = select_option_dialog(
        title=msg(lang, en="Setup: Chat model", ru="Настройка: Модель чата"),
        text=msg(
            lang,
            en=f"Select {spec.label} model or choose manual input.",
            ru=f"Выберите модель {spec.label} или ручной ввод.",
        ),
        options=options,
        default=selected_default,
        hint_text=single_hint(lang),
    )
    if selected not in {"manual", "custom"}:
        return selected

    while True:
        if provider_id.value == "custom":
            suggested_default = "custom-model"
        elif default_model.strip():
            suggested_default = default_model.strip()
        elif preset_options:
            suggested_default = preset_options[0]
        else:
            suggested_default = "model-id"
        model = str(
            typer.prompt(
                msg(lang, en="Chat model", ru="Модель чата"),
                default=default_model or suggested_default,
            )
        ).strip()
        if model:
            return model
        typer.echo(
            msg(
                lang,
                en="Chat model cannot be empty.",
                ru="Модель чата не может быть пустой.",
            )
        )


def prompt_thinking_level(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt default reasoning level via selector with validated fallback."""

    selected_default = default if default in THINKING_LEVEL_CHOICES else "medium"
    selected = select_option_dialog(
        title=msg(lang, en="Setup: Thinking level", ru="Настройка: Уровень раздумия"),
        text=msg(
            lang,
            en="Select the default reasoning budget for this profile.",
            ru="Выберите уровень раздумия по умолчанию для этого профиля.",
        ),
        options=list(THINKING_LEVEL_CHOICES),
        default=selected_default,
        hint_text=single_hint(lang),
    )
    if selected in THINKING_LEVEL_CHOICES:
        return selected
    while True:
        value = str(
            typer.prompt(
                msg(
                    lang,
                    en="Default thinking level (low/medium/high/very_high)",
                    ru="Уровень раздумия по умолчанию (low/medium/high/very_high)",
                ),
                default=selected_default,
            )
        ).strip().lower()
        if value in THINKING_LEVEL_CHOICES:
            return value
        typer.echo(
            msg(
                lang,
                en="Invalid thinking level: choose low, medium, high, or very_high.",
                ru="Некорректный уровень раздумия: выберите low, medium, high или very_high.",
            )
        )


def prompt_custom_interface(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt API interface for custom provider runtime."""

    selected_default = default if default == "openai" else "openai"
    selected = select_option_dialog(
        title=msg(lang, en="Setup: Custom interface", ru="Настройка: Интерфейс custom API"),
        text=msg(
            lang,
            en="Select API interface for custom provider.",
            ru="Выберите API-интерфейс для custom провайдера.",
        ),
        options=["openai"],
        default=selected_default,
        hint_text=single_hint(lang),
    )
    if selected == "openai":
        return selected
    return "openai"


def prompt_proxy_config(
    *,
    default_type: str,
    default_url: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> tuple[str, str]:
    """Prompt proxy enable/type/url for setup runtime config."""

    should_use_proxy = confirm_space(
        question=msg(
            lang,
            en="Use proxy for LLM requests?",
            ru="Использовать прокси для запросов к LLM?",
        ),
        default=default_type != "none",
        title=msg(lang, en="Setup: Proxy", ru="Настройка: Прокси"),
        yes_label=yes_label(lang),
        no_label=no_label(lang),
        hint_text=single_hint(lang),
    )
    if not should_use_proxy:
        return "none", ""
    selected = select_option_dialog(
        title=msg(lang, en="Setup: Proxy type", ru="Настройка: Тип прокси"),
        text=msg(
            lang,
            en="Select proxy type for LLM requests.",
            ru="Выберите тип прокси для запросов к LLM.",
        ),
        options=[HTTP_PROXY_TYPE, SOCKS5_PROXY_TYPE, SOCKS5H_PROXY_TYPE],
        default=(default_type if default_type in PROXY_TYPE_CHOICES else HTTP_PROXY_TYPE),
        hint_text=single_hint(lang),
    )
    proxy_type = selected if selected in PROXY_TYPE_CHOICES else HTTP_PROXY_TYPE
    if proxy_type == HTTP_PROXY_TYPE:
        recommended = default_url if default_type == HTTP_PROXY_TYPE else "http://127.0.0.1:7890"
    elif proxy_type == SOCKS5H_PROXY_TYPE:
        recommended = default_url if default_type == SOCKS5H_PROXY_TYPE else "socks5h://127.0.0.1:1080"
    else:
        recommended = default_url if default_type == SOCKS5_PROXY_TYPE else "socks5://127.0.0.1:1080"

    typer.echo(
        msg(
            lang,
            en=(
                "Examples: host:port | host:port:user:pass | "
                "http://user:pass@host:port | socks5://user:pass@host:port"
            ),
            ru=(
                "Примеры: host:port | host:port:user:pass | "
                "http://user:pass@host:port | socks5://user:pass@host:port"
            ),
        )
    )
    while True:
        proxy_url = str(
            typer.prompt(
                msg(lang, en="LLM proxy URL", ru="URL LLM-прокси"),
                default=recommended,
            )
        ).strip()
        if proxy_url:
            return proxy_type, proxy_url
        typer.echo(
            msg(
                lang,
                en="LLM proxy URL cannot be empty",
                ru="URL LLM-прокси не может быть пустым",
            )
        )


def prompt_secret_ack(*, lang: PromptLanguage = PromptLanguage.EN) -> bool:
    """Prompt security acknowledgment for interactive setup flow."""

    return confirm_space(
        question=msg(
            lang,
            en="I understand responsibility for tokens, secrets, and automations. Continue?",
            ru="Я понимаю ответственность за токены, секреты и автоматизации. Продолжить?",
        ),
        default=False,
        title=msg(lang, en="Setup: Security acknowledgment", ru="Настройка: Подтверждение безопасности"),
        yes_label=yes_label(lang),
        no_label=no_label(lang),
        hint_text=single_hint(lang),
    )


def prompt_confirm(
    *,
    question: str,
    title: str,
    default: bool,
    lang: PromptLanguage = PromptLanguage.EN,
) -> bool:
    """Prompt one generic yes/no question in interactive CLI flows."""

    return confirm_space(
        question=question,
        default=default,
        title=title,
        yes_label=yes_label(lang),
        no_label=no_label(lang),
        hint_text=single_hint(lang),
    )


def prompt_nginx_enabled(*, default: bool, lang: PromptLanguage = PromptLanguage.EN) -> bool:
    """Prompt nginx flag using inline confirm selector."""

    return confirm_space(
        question=msg(
            lang,
            en="Enable nginx sidecar configuration?",
            ru="Включить конфигурацию nginx sidecar?",
        ),
        default=default,
        title=msg(lang, en="Setup: Nginx", ru="Настройка: Nginx"),
        yes_label=yes_label(lang),
        no_label=no_label(lang),
        hint_text=single_hint(lang),
    )


def prompt_nginx_public_host(
    *,
    endpoint_label: str,
    internal_port: int,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt public domain/IP for one nginx-proxied endpoint."""

    while True:
        value = str(
            typer.prompt(
                msg(
                    lang,
                    en=f"Public domain or IP for {endpoint_label} ({internal_port})",
                    ru=f"Публичный домен или IP для {endpoint_label} ({internal_port})",
                ),
                default=default,
            )
        ).strip()
        if value:
            return value
        typer.echo(
            msg(
                lang,
                en=f"Public domain or IP for {endpoint_label} cannot be empty.",
                ru=f"Публичный домен или IP для {endpoint_label} не может быть пустым.",
            )
        )


def prompt_nginx_https_enabled(
    *,
    endpoint_label: str,
    host: str,
    default: bool,
    lang: PromptLanguage = PromptLanguage.EN,
) -> bool:
    """Prompt HTTPS enable flag for one domain-backed nginx endpoint."""

    return confirm_space(
        question=msg(
            lang,
            en=f"Enable HTTPS via Certbot for {endpoint_label} ({host})?",
            ru=f"Включить HTTPS через Certbot для {endpoint_label} ({host})?",
        ),
        default=default,
        title=msg(lang, en="Setup: HTTPS", ru="Настройка: HTTPS"),
        yes_label=yes_label(lang),
        no_label=no_label(lang),
        hint_text=single_hint(lang),
    )


def prompt_certbot_email(
    *,
    default: str,
    lang: PromptLanguage = PromptLanguage.EN,
) -> str:
    """Prompt certificate registration email for Certbot."""

    while True:
        value = str(
            typer.prompt(
                msg(
                    lang,
                    en="Certbot email",
                    ru="Email для Certbot",
                ),
                default=default,
            )
        ).strip()
        if value:
            return value
        typer.echo(
            msg(
                lang,
                en="Certbot email cannot be empty when HTTPS is enabled.",
                ru="Email для Certbot не может быть пустым, когда включён HTTPS.",
            )
        )
