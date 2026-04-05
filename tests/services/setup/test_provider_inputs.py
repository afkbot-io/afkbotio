"""Tests for setup provider input helpers."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.services.setup.provider_inputs import resolve_provider_base_url_default
from afkbot.settings import Settings


def test_resolve_provider_base_url_default_prefers_claude_provider_value(tmp_path: Path) -> None:
    """Claude setup should read provider-specific base URL default first."""

    settings = Settings(root_dir=tmp_path)
    resolved = resolve_provider_base_url_default(
        defaults={"AFKBOT_CLAUDE_BASE_URL": "https://claude.example/v1"},
        settings=settings,
        provider_id=LLMProviderId.CLAUDE,
    )

    assert resolved == "https://claude.example/v1"


def test_resolve_provider_base_url_default_prefers_openai_codex_provider_value(
    tmp_path: Path,
) -> None:
    """OpenAI Codex setup should read provider-specific base URL default first."""

    settings = Settings(root_dir=tmp_path)
    resolved = resolve_provider_base_url_default(
        defaults={"AFKBOT_OPENAI_CODEX_BASE_URL": "https://chatgpt.com/backend-api/codex"},
        settings=settings,
        provider_id=LLMProviderId.OPENAI_CODEX,
    )

    assert resolved == "https://chatgpt.com/backend-api/codex"


def test_resolve_provider_base_url_default_prefers_moonshot_provider_value(tmp_path: Path) -> None:
    """Moonshot setup should read provider-specific base URL default first."""

    settings = Settings(root_dir=tmp_path)
    resolved = resolve_provider_base_url_default(
        defaults={"AFKBOT_MOONSHOT_BASE_URL": "https://moonshot.example/v1"},
        settings=settings,
        provider_id=LLMProviderId.MOONSHOT,
    )

    assert resolved == "https://moonshot.example/v1"


def test_resolve_provider_base_url_default_falls_back_to_global_override(tmp_path: Path) -> None:
    """Global base URL override should apply when provider-specific value is empty."""

    settings = Settings(root_dir=tmp_path)
    resolved = resolve_provider_base_url_default(
        defaults={
            "AFKBOT_CLAUDE_BASE_URL": "",
            "AFKBOT_LLM_BASE_URL": "https://global-gateway.example/v1",
        },
        settings=settings,
        provider_id=LLMProviderId.CLAUDE,
    )

    assert resolved == "https://global-gateway.example/v1"
