"""Single source of truth for supported LLM providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LLMProviderId(StrEnum):
    """Supported provider identifiers."""

    OPENROUTER = "openrouter"
    OPENAI = "openai"
    CLAUDE = "claude"
    MOONSHOT = "moonshot"
    DEEPSEEK = "deepseek"
    XAI = "xai"
    QWEN = "qwen"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Provider metadata used by CLI, install, and runtime factory."""

    id: LLMProviderId
    label: str
    default_base_url: str
    base_url_env_name: str
    verify_path: str
    model_choices: tuple[str, ...]
    api_key_env_names: tuple[str, ...]
    interface_choices: tuple[str, ...] = ("openai",)


_PROVIDER_SPECS: dict[LLMProviderId, ProviderSpec] = {
    LLMProviderId.OPENROUTER: ProviderSpec(
        id=LLMProviderId.OPENROUTER,
        label="OpenRouter",
        default_base_url="https://openrouter.ai/api/v1",
        base_url_env_name="AFKBOT_OPENROUTER_BASE_URL",
        verify_path="/key",
        model_choices=(
            "xiaomi/mimo-v2-pro",
            "stepfun/step-3.5-flash:free",
            "minimax/minimax-m2.7",
            "deepseek/deepseek-v3.2",
            "qwen/qwen3.6-plus-preview:free",
            "anthropic/claude-sonnet-4.6",
            "anthropic/claude-opus-4.6",
            "google/gemini-3-flash-preview",
            "z-ai/glm-5-turbo",
            "x-ai/grok-4.1-fast",
            "minimax/minimax-m2.5",
            "xiaomi/mimo-v2-omni",
            "google/gemini-2.5-flash-lite",
            "nvidia/nemotron-3-super-120b-a12b:free",
            "google/gemini-2.5-flash",
            "moonshotai/kimi-k2.5",
            "openai/gpt-oss-120b",
            "z-ai/glm-5",
            "openai/gpt-5.4",
            "anthropic/claude-sonnet-4.5",
        ),
        api_key_env_names=("AFKBOT_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"),
    ),
    LLMProviderId.OPENAI: ProviderSpec(
        id=LLMProviderId.OPENAI,
        label="OpenAI",
        default_base_url="https://api.openai.com/v1",
        base_url_env_name="AFKBOT_OPENAI_BASE_URL",
        verify_path="/models",
        model_choices=(
            "gpt-4o-mini",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "o4-mini",
        ),
        api_key_env_names=("AFKBOT_OPENAI_API_KEY", "OPENAI_API_KEY"),
    ),
    LLMProviderId.CLAUDE: ProviderSpec(
        id=LLMProviderId.CLAUDE,
        label="Claude (Anthropic)",
        default_base_url="https://api.anthropic.com/v1",
        base_url_env_name="AFKBOT_CLAUDE_BASE_URL",
        verify_path="/models",
        model_choices=(
            "claude-sonnet-4-6",
            "claude-opus-4-6",
            "claude-sonnet-4-5",
        ),
        api_key_env_names=("AFKBOT_CLAUDE_API_KEY", "ANTHROPIC_API_KEY"),
    ),
    LLMProviderId.MOONSHOT: ProviderSpec(
        id=LLMProviderId.MOONSHOT,
        label="Moonshot (Kimi)",
        default_base_url="https://api.moonshot.ai/v1",
        base_url_env_name="AFKBOT_MOONSHOT_BASE_URL",
        verify_path="/models",
        model_choices=(
            "kimi-k2.5",
            "kimi-k2-thinking",
            "kimi-k2-turbo-preview",
        ),
        api_key_env_names=("AFKBOT_MOONSHOT_API_KEY", "MOONSHOT_API_KEY"),
    ),
    LLMProviderId.DEEPSEEK: ProviderSpec(
        id=LLMProviderId.DEEPSEEK,
        label="DeepSeek",
        default_base_url="https://api.deepseek.com",
        base_url_env_name="AFKBOT_DEEPSEEK_BASE_URL",
        verify_path="/models",
        model_choices=(
            "deepseek-chat",
            "deepseek-reasoner",
        ),
        api_key_env_names=("AFKBOT_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    ),
    LLMProviderId.XAI: ProviderSpec(
        id=LLMProviderId.XAI,
        label="xAI (Grok)",
        default_base_url="https://api.x.ai/v1",
        base_url_env_name="AFKBOT_XAI_BASE_URL",
        verify_path="/models",
        model_choices=(
            "grok-3-mini",
            "grok-3-beta",
        ),
        api_key_env_names=("AFKBOT_XAI_API_KEY", "XAI_API_KEY"),
    ),
    LLMProviderId.QWEN: ProviderSpec(
        id=LLMProviderId.QWEN,
        label="Qwen",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        base_url_env_name="AFKBOT_QWEN_BASE_URL",
        verify_path="/models",
        model_choices=(
            "qwen-max",
            "qwen-plus",
            "qwen-turbo",
        ),
        api_key_env_names=("AFKBOT_QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    ),
    LLMProviderId.CUSTOM: ProviderSpec(
        id=LLMProviderId.CUSTOM,
        label="Custom",
        default_base_url="",
        base_url_env_name="AFKBOT_CUSTOM_BASE_URL",
        verify_path="/models",
        model_choices=(),
        api_key_env_names=("AFKBOT_CUSTOM_API_KEY",),
        interface_choices=("openai",),
    ),
}


def list_supported_providers(*, include_none: bool = True) -> tuple[LLMProviderId, ...]:
    """Return provider ids in stable UX order."""

    _ = include_none
    return (
        LLMProviderId.OPENROUTER,
        LLMProviderId.OPENAI,
        LLMProviderId.CLAUDE,
        LLMProviderId.MOONSHOT,
        LLMProviderId.DEEPSEEK,
        LLMProviderId.XAI,
        LLMProviderId.QWEN,
        LLMProviderId.CUSTOM,
    )


def parse_provider(value: str) -> LLMProviderId:
    """Parse raw provider value into enum with explicit error."""

    normalized = value.strip().lower()
    for provider in list_supported_providers(include_none=False):
        if provider.value == normalized:
            return provider
    raise ValueError(f"Unsupported LLM provider: {value}")


def get_provider_spec(provider: LLMProviderId) -> ProviderSpec:
    """Return provider specification by id."""

    return _PROVIDER_SPECS[provider]


def provider_choices(*, include_none: bool = True) -> tuple[str, ...]:
    """Return provider values for CLI choices."""

    return tuple(item.value for item in list_supported_providers(include_none=include_none))
