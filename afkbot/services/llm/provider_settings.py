"""Provider-specific settings resolution for LLM runtime."""

from __future__ import annotations

from dataclasses import dataclass

from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.settings import Settings


@dataclass(frozen=True, slots=True)
class ResolvedProviderDebugInfo:
    """Redacted provider diagnostics derived from effective runtime settings."""

    api_key_present: bool


def describe_provider_debug_info(
    *,
    settings: Settings,
    provider_id: LLMProviderId,
) -> ResolvedProviderDebugInfo:
    """Return redacted provider diagnostics for temporary operator debugging."""

    api_key = resolve_api_key(settings=settings, provider_id=provider_id)
    return ResolvedProviderDebugInfo(
        api_key_present=bool((api_key or "").strip()),
    )


def resolve_api_key(*, settings: Settings, provider_id: LLMProviderId) -> str | None:
    """Resolve the effective provider API key with generic fallback support."""

    if provider_id == LLMProviderId.OPENROUTER:
        return _normalized_optional_text(settings.openrouter_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.OPENAI:
        return _normalized_optional_text(settings.openai_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.OPENAI_CODEX:
        return _normalized_optional_text(settings.openai_codex_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.CLAUDE:
        return _normalized_optional_text(settings.claude_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.MOONSHOT:
        return _normalized_optional_text(settings.moonshot_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.DEEPSEEK:
        return _normalized_optional_text(settings.deepseek_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.XAI:
        return _normalized_optional_text(settings.xai_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.QWEN:
        return _normalized_optional_text(settings.qwen_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.MINIMAX_PORTAL:
        return _normalized_optional_text(settings.minimax_portal_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.GITHUB_COPILOT:
        return _normalized_optional_text(settings.github_copilot_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    if provider_id == LLMProviderId.CUSTOM:
        return _normalized_optional_text(settings.custom_api_key) or _normalized_optional_text(
            settings.llm_api_key,
        )
    return None


def resolve_base_url(*, settings: Settings, provider_id: LLMProviderId) -> str:
    """Resolve effective provider base URL with optional global override."""

    if provider_id == LLMProviderId.OPENROUTER:
        return _normalized_optional_text(settings.openrouter_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.OPENAI:
        return _normalized_optional_text(settings.openai_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.OPENAI_CODEX:
        return _normalized_optional_text(settings.openai_codex_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.CLAUDE:
        return _normalized_optional_text(settings.claude_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.MOONSHOT:
        return _normalized_optional_text(settings.moonshot_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.DEEPSEEK:
        return _normalized_optional_text(settings.deepseek_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.XAI:
        return _normalized_optional_text(settings.xai_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.QWEN:
        return _normalized_optional_text(settings.qwen_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.MINIMAX_PORTAL:
        return _normalized_optional_text(settings.minimax_portal_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.GITHUB_COPILOT:
        return _normalized_optional_text(settings.github_copilot_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    if provider_id == LLMProviderId.CUSTOM:
        return _normalized_optional_text(settings.custom_base_url) or _normalized_optional_text(
            settings.llm_base_url,
        ) or ""
    return ""


def _normalized_optional_text(value: object) -> str | None:
    """Convert dynamic settings values into a strict optional text shape."""

    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None
