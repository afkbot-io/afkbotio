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
        title=msg(lang, en="Setup: AI provider", ru="Настройка: AI-провайдер"),
        text=msg(
            lang,
            en="Choose which AI service AFKBOT should use for new chats by default.",
            ru="Выберите, какой AI-сервис AFKBOT будет использовать в новых чатах по умолчанию.",
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
                    en=f"AI provider ({options})",
                    ru=f"AI-провайдер ({options})",
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
            en=f"Choose the {spec.label} model AFKBOT should use. Pick manual input if your model ID is not listed.",
            ru=f"Выберите модель {spec.label}, которую AFKBOT будет использовать. Выберите ручной ввод, если нужного ID модели нет в списке.",
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
                msg(lang, en="Model ID", ru="ID модели"),
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
        title=msg(lang, en="Setup: Reasoning effort", ru="Настройка: Глубина рассуждения"),
        text=msg(
            lang,
            en="Choose how much effort the agent should spend thinking before it acts.",
            ru="Выберите, насколько глубоко агент должен обдумывать ответ перед действием.",
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
                    en="Default reasoning effort (low/medium/high/very_high)",
                    ru="Глубина рассуждения по умолчанию (low/medium/high/very_high)",
                ),
                default=selected_default,
            )
        ).strip().lower()
        if value in THINKING_LEVEL_CHOICES:
            return value
        typer.echo(
            msg(
                lang,
                en="Invalid reasoning effort: choose low, medium, high, or very_high.",
                ru="Некорректная глубина рассуждения: выберите low, medium, high или very_high.",
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
            en="Choose the API format your custom endpoint speaks.",
            ru="Выберите формат API, который поддерживает ваш custom endpoint.",
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
            en="Route provider requests through a proxy?",
            ru="Пропускать запросы к провайдеру через прокси?",
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
            en="Choose which proxy protocol AFKBOT should use for provider requests.",
            ru="Выберите, какой протокол прокси AFKBOT должен использовать для запросов к провайдеру.",
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
            en=(
                "AFKBOT can use your API keys, local files, and automations on this machine. "
                "Continue only if you understand and accept that responsibility."
            ),
            ru=(
                "AFKBOT может использовать ваши API-ключи, локальные файлы и автоматизации на этой машине. "
                "Продолжайте, только если понимаете и принимаете эту ответственность."
            ),
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
            en="Generate nginx reverse-proxy configuration?",
            ru="Сгенерировать конфигурацию nginx как reverse proxy?",
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
