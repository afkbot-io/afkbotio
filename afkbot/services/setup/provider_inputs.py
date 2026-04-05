"""Provider and generic prompt-driven input resolvers for setup CLI."""

from __future__ import annotations

import typer

from afkbot.cli.presentation.setup_prompts import (
    PromptLanguage,
    msg,
    prompt_chat_model,
    prompt_custom_interface,
    prompt_provider,
    prompt_thinking_level,
)
from afkbot.services.llm.provider_catalog import (
    LLMProviderId,
    get_provider_spec,
    provider_choices,
)
from afkbot.services.llm.reasoning import normalize_thinking_level
from afkbot.services.setup.provider_network import (
    resolve_nginx_enabled,
    resolve_nginx_plan,
    resolve_port,
    resolve_proxy,
)
from afkbot.services.setup.provider_secrets import (
    read_secret_file,
    resolve_api_key,
    resolve_credentials_master_keys,
)
from afkbot.settings import Settings

__all__ = [
    "read_secret_file",
    "resolve_api_key",
    "resolve_credentials_master_keys",
    "resolve_custom_interface",
    "resolve_model",
    "resolve_nginx_enabled",
    "resolve_nginx_plan",
    "resolve_port",
    "resolve_provider",
    "resolve_provider_base_url_default",
    "resolve_proxy",
    "resolve_thinking_level",
    "resolve_text",
]


def resolve_provider(
    *,
    value: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve setup provider selection."""

    if value is not None:
        provider = value.strip().lower()
    elif interactive:
        provider = prompt_provider(default=default, lang=lang).strip().lower()
    else:
        provider = default.strip().lower()
    allowed = provider_choices(include_none=False)
    if provider not in allowed:
        available = ", ".join(allowed)
        raise typer.BadParameter(
            msg(
                lang,
                en=f"AI provider must be one of: {available}",
                ru=f"AI-провайдер должен быть одним из: {available}",
            )
        )
    return provider


def resolve_model(
    *,
    provider: str,
    value: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve chat model selection for the chosen provider."""

    if value is not None:
        normalized = value.strip()
    elif interactive:
        normalized = prompt_chat_model(
            provider=provider,
            default_model=default,
            lang=lang,
        ).strip()
    else:
        normalized = str(default).strip()
    if not normalized:
        raise typer.BadParameter(
            msg(lang, en="Chat model cannot be empty", ru="Модель чата не может быть пустой")
        )
    return normalized


def resolve_custom_interface(
    *,
    value: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve interface subtype for custom LLM providers."""

    if value is not None:
        normalized = value.strip().lower()
    elif interactive:
        normalized = prompt_custom_interface(default=default, lang=lang).strip().lower()
    else:
        normalized = default.strip().lower() or "openai"
    if normalized != "openai":
        raise typer.BadParameter(
            msg(
                lang,
                en="Custom interface must be: openai",
                ru="Параметр custom interface должен быть: openai",
            )
        )
    return normalized


def resolve_text(
    *,
    value: str | None,
    interactive: bool,
    prompt: str,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve a required text field from flag or prompt."""

    if value is not None:
        normalized = value.strip()
    elif interactive:
        normalized = typer.prompt(prompt, default=default).strip()
    else:
        normalized = str(default).strip()
    if not normalized:
        raise typer.BadParameter(
            msg(
                lang,
                en=f"{prompt} cannot be empty",
                ru=f"{prompt} не может быть пустым",
            )
        )
    return normalized


def resolve_thinking_level(
    *,
    value: str | None,
    interactive: bool,
    default: str,
    lang: PromptLanguage,
) -> str:
    """Resolve one default reasoning level from flag or selector prompt."""

    if value is not None:
        normalized = normalize_thinking_level(value)
    elif interactive:
        normalized = normalize_thinking_level(
            prompt_thinking_level(default=default, lang=lang).strip().lower()
        )
    else:
        normalized = normalize_thinking_level(default)
    if normalized is None:
        raise typer.BadParameter(
            msg(
                lang,
                en="Reasoning effort must be one of: low, medium, high, very_high",
                ru="Глубина рассуждения должна быть одной из: low, medium, high, very_high",
            )
        )
    return normalized


def resolve_provider_base_url_default(
    *,
    defaults: dict[str, str],
    settings: Settings,
    provider_id: LLMProviderId,
) -> str:
    """Resolve default base URL for selected provider from persisted store/settings."""

    spec = get_provider_spec(provider_id)
    if provider_id == LLMProviderId.OPENROUTER:
        provider_value = defaults.get("AFKBOT_OPENROUTER_BASE_URL", settings.openrouter_base_url).strip()
    elif provider_id == LLMProviderId.OPENAI:
        provider_value = defaults.get("AFKBOT_OPENAI_BASE_URL", settings.openai_base_url).strip()
    elif provider_id == LLMProviderId.OPENAI_CODEX:
        provider_value = defaults.get("AFKBOT_OPENAI_CODEX_BASE_URL", settings.openai_codex_base_url).strip()
    elif provider_id == LLMProviderId.CLAUDE:
        provider_value = defaults.get("AFKBOT_CLAUDE_BASE_URL", settings.claude_base_url).strip()
    elif provider_id == LLMProviderId.MOONSHOT:
        provider_value = defaults.get("AFKBOT_MOONSHOT_BASE_URL", settings.moonshot_base_url).strip()
    elif provider_id == LLMProviderId.DEEPSEEK:
        provider_value = defaults.get("AFKBOT_DEEPSEEK_BASE_URL", settings.deepseek_base_url).strip()
    elif provider_id == LLMProviderId.XAI:
        provider_value = defaults.get("AFKBOT_XAI_BASE_URL", settings.xai_base_url).strip()
    elif provider_id == LLMProviderId.QWEN:
        provider_value = defaults.get("AFKBOT_QWEN_BASE_URL", settings.qwen_base_url).strip()
    elif provider_id == LLMProviderId.MINIMAX_PORTAL:
        provider_value = defaults.get(
            "AFKBOT_MINIMAX_PORTAL_BASE_URL",
            settings.minimax_portal_base_url,
        ).strip()
    elif provider_id == LLMProviderId.GITHUB_COPILOT:
        provider_value = defaults.get(
            "AFKBOT_GITHUB_COPILOT_BASE_URL",
            settings.github_copilot_base_url,
        ).strip()
    elif provider_id == LLMProviderId.CUSTOM:
        provider_value = defaults.get("AFKBOT_CUSTOM_BASE_URL", settings.custom_base_url).strip()
    else:
        provider_value = ""

    if provider_value:
        return provider_value

    override_value = defaults.get("AFKBOT_LLM_BASE_URL", settings.llm_base_url or "")
    if override_value.strip():
        return override_value.strip()
    return spec.default_base_url
