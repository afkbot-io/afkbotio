"""Tests for LLM provider catalog metadata and ordering."""

from __future__ import annotations

from afkbot.services.llm.provider_catalog import (
    LLMProviderId,
    get_provider_spec,
    list_supported_providers,
    parse_provider,
    provider_choices,
)


def test_supported_providers_include_claude_and_moonshot_in_stable_order() -> None:
    """Provider list should expose new providers in deterministic setup order."""

    assert list_supported_providers(include_none=False) == (
        LLMProviderId.OPENROUTER,
        LLMProviderId.OPENAI,
        LLMProviderId.OPENAI_CODEX,
        LLMProviderId.CLAUDE,
        LLMProviderId.MOONSHOT,
        LLMProviderId.DEEPSEEK,
        LLMProviderId.XAI,
        LLMProviderId.QWEN,
        LLMProviderId.MINIMAX_PORTAL,
        LLMProviderId.GITHUB_COPILOT,
        LLMProviderId.CUSTOM,
    )
    assert provider_choices(include_none=False) == (
        "openrouter",
        "openai",
        "openai-codex",
        "claude",
        "moonshot",
        "deepseek",
        "xai",
        "qwen",
        "minimax-portal",
        "github-copilot",
        "custom",
    )


def test_openrouter_model_choices_match_current_top20() -> None:
    """OpenRouter presets should track the curated top-20 list used in setup UI."""

    spec = get_provider_spec(LLMProviderId.OPENROUTER)

    assert spec.model_choices == (
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
    )


def test_new_provider_specs_have_provider_native_defaults() -> None:
    """Claude and Moonshot presets should default to models from their own families."""

    claude_spec = get_provider_spec(LLMProviderId.CLAUDE)
    moonshot_spec = get_provider_spec(LLMProviderId.MOONSHOT)

    assert claude_spec.default_base_url == "https://api.anthropic.com/v1"
    assert claude_spec.model_choices[0] == "claude-sonnet-4-6"
    assert "ANTHROPIC_API_KEY" in claude_spec.api_key_env_names

    assert moonshot_spec.default_base_url == "https://api.moonshot.ai/v1"
    assert moonshot_spec.model_choices[0] == "kimi-k2.5"
    assert "MOONSHOT_API_KEY" in moonshot_spec.api_key_env_names


def test_parse_provider_accepts_new_provider_ids() -> None:
    """Provider parser should map new provider names exactly."""

    assert parse_provider("claude") == LLMProviderId.CLAUDE
    assert parse_provider("moonshot") == LLMProviderId.MOONSHOT
    assert parse_provider("openai-codex") == LLMProviderId.OPENAI_CODEX
    assert parse_provider("minimax-portal") == LLMProviderId.MINIMAX_PORTAL
    assert parse_provider("github-copilot") == LLMProviderId.GITHUB_COPILOT
