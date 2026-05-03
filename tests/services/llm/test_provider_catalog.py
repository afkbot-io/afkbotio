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
        LLMProviderId.MOONSHOT_CN,
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
        "moonshot-cn",
        "deepseek",
        "xai",
        "qwen",
        "minimax-portal",
        "github-copilot",
        "custom",
    )


def test_openrouter_model_choices_match_current_curated_list() -> None:
    """OpenRouter presets should track the curated list used in setup UI."""

    spec = get_provider_spec(LLMProviderId.OPENROUTER)

    assert spec.model_choices == (
        "xiaomi/mimo-v2-pro",
        "openai/gpt-5.4",
        "openai/gpt-5.2",
        "openai/gpt-5.2-codex",
        "anthropic/claude-sonnet-4.6",
        "anthropic/claude-opus-4.6",
        "x-ai/grok-code-fast-1",
        "x-ai/grok-4.1-fast",
        "minimax/minimax-m2.7",
        "deepseek/deepseek-v3.2",
        "qwen/qwen3.6-plus",
        "qwen/qwen3.6-max-preview",
        "google/gemini-3-flash-preview",
        "google/gemini-2.5-flash",
        "google/gemini-2.5-flash-lite",
        "z-ai/glm-5",
        "z-ai/glm-5-turbo",
        "moonshotai/kimi-k2.6",
        "moonshotai/kimi-k2.5",
        "minimax/minimax-m2.5",
        "xiaomi/mimo-v2-omni",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "openai/gpt-oss-120b",
        "anthropic/claude-sonnet-4.5",
        "stepfun/step-3.5-flash",
    )


def test_new_provider_specs_have_provider_native_defaults() -> None:
    """Claude and Moonshot presets should default to models from their own families."""

    claude_spec = get_provider_spec(LLMProviderId.CLAUDE)
    moonshot_spec = get_provider_spec(LLMProviderId.MOONSHOT)
    moonshot_cn_spec = get_provider_spec(LLMProviderId.MOONSHOT_CN)

    assert claude_spec.default_base_url == "https://api.anthropic.com/v1"
    assert claude_spec.model_choices[0] == "claude-sonnet-4-6"
    assert "ANTHROPIC_API_KEY" in claude_spec.api_key_env_names

    assert moonshot_spec.default_base_url == "https://api.moonshot.ai/v1"
    assert moonshot_spec.model_choices[0] == "kimi-k2.6"
    assert "kimi-k2.5" in moonshot_spec.model_choices
    assert "MOONSHOT_API_KEY" in moonshot_spec.api_key_env_names

    assert moonshot_cn_spec.default_base_url == "https://api.moonshot.cn/v1"
    assert moonshot_cn_spec.model_choices[0] == "kimi-k2.6"
    assert "kimi-k2.5" in moonshot_cn_spec.model_choices
    assert "MOONSHOT_CN_API_KEY" in moonshot_cn_spec.api_key_env_names


def test_provider_model_choices_include_current_primary_models() -> None:
    """Provider model presets should include current primary chat/coding models."""

    assert "gpt-5.2" in get_provider_spec(LLMProviderId.OPENAI).model_choices
    assert "gpt-5.2-codex" in get_provider_spec(LLMProviderId.OPENAI_CODEX).model_choices
    assert "claude-opus-4-1" in get_provider_spec(LLMProviderId.CLAUDE).model_choices
    assert "deepseek-v4-flash" in get_provider_spec(LLMProviderId.DEEPSEEK).model_choices
    assert "deepseek-v4-pro" in get_provider_spec(LLMProviderId.DEEPSEEK).model_choices
    assert "grok-4.3" in get_provider_spec(LLMProviderId.XAI).model_choices
    assert "qwen3.6-plus" in get_provider_spec(LLMProviderId.QWEN).model_choices
    assert "MiniMax-M2.5-highspeed" in get_provider_spec(LLMProviderId.MINIMAX_PORTAL).model_choices
    assert "gpt-5.5" in get_provider_spec(LLMProviderId.GITHUB_COPILOT).model_choices
    assert "gpt-5.4" in get_provider_spec(LLMProviderId.GITHUB_COPILOT).model_choices
    assert "claude-sonnet-4.6" in get_provider_spec(LLMProviderId.GITHUB_COPILOT).model_choices


def test_parse_provider_accepts_new_provider_ids() -> None:
    """Provider parser should map new provider names exactly."""

    assert parse_provider("claude") == LLMProviderId.CLAUDE
    assert parse_provider("moonshot") == LLMProviderId.MOONSHOT
    assert parse_provider("moonshot-cn") == LLMProviderId.MOONSHOT_CN
    assert parse_provider("openai-codex") == LLMProviderId.OPENAI_CODEX
    assert parse_provider("minimax-portal") == LLMProviderId.MINIMAX_PORTAL
    assert parse_provider("github-copilot") == LLMProviderId.GITHUB_COPILOT
