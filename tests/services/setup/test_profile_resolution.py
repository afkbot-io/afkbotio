"""Tests for setup/profile runtime resolution helpers."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.setup.profile_resolution import resolve_profile_runtime_core
from afkbot.settings import Settings


def test_resolve_profile_runtime_core_skips_base_url_prompt_for_standard_provider_defaults(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive setup should not ask for base URL when the standard provider default is sufficient."""

    settings = Settings(root_dir=tmp_path)
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_provider",
        lambda **kwargs: "openai",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_model",
        lambda **kwargs: "gpt-4o-mini",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_thinking_level",
        lambda **kwargs: "medium",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_proxy",
        lambda **kwargs: ("none", ""),
    )

    def _fake_resolve_text(*, value, interactive, prompt, default, lang):
        del value, prompt, lang
        observed["interactive"] = interactive
        observed["default"] = default
        return default

    monkeypatch.setattr("afkbot.services.setup.profile_resolution.resolve_text", _fake_resolve_text)

    resolved = resolve_profile_runtime_core(
        interactive=True,
        lang=PromptLanguage.EN,
        settings=settings,
        provider_value=None,
        model_value=None,
        thinking_level_value=None,
        thinking_level_prompt="Reasoning effort",
        base_url_value=None,
        base_url_prompt="Provider base URL",
        custom_interface_value=None,
        proxy_type_value=None,
        proxy_url_value=None,
        planning_mode_value=None,
        default_provider="openai",
        default_model="gpt-4o-mini",
        default_thinking_level="medium",
        default_base_url="https://api.openai.com/v1",
        default_custom_interface="openai",
        default_proxy_type="none",
        default_proxy_url="",
        default_planning_mode=None,
    )

    assert observed == {
        "interactive": False,
        "default": "https://api.openai.com/v1",
    }
    assert resolved.llm_base_url == "https://api.openai.com/v1"


def test_resolve_profile_runtime_core_prompts_for_base_url_for_custom_provider(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive setup should still ask for base URL when the operator picked a custom provider."""

    settings = Settings(root_dir=tmp_path)
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_provider",
        lambda **kwargs: "custom",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_model",
        lambda **kwargs: "custom-model",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_thinking_level",
        lambda **kwargs: "medium",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_custom_interface",
        lambda **kwargs: "openai",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_proxy",
        lambda **kwargs: ("none", ""),
    )

    def _fake_resolve_text(*, value, interactive, prompt, default, lang):
        del value, prompt, lang
        observed["interactive"] = interactive
        observed["default"] = default
        return "https://gateway.example/v1"

    monkeypatch.setattr("afkbot.services.setup.profile_resolution.resolve_text", _fake_resolve_text)

    resolved = resolve_profile_runtime_core(
        interactive=True,
        lang=PromptLanguage.EN,
        settings=settings,
        provider_value=None,
        model_value=None,
        thinking_level_value=None,
        thinking_level_prompt="Reasoning effort",
        base_url_value=None,
        base_url_prompt="Provider base URL",
        custom_interface_value=None,
        proxy_type_value=None,
        proxy_url_value=None,
        planning_mode_value=None,
        default_provider="custom",
        default_model="custom-model",
        default_thinking_level="medium",
        default_base_url="https://custom.example/v1",
        default_custom_interface="openai",
        default_proxy_type="none",
        default_proxy_url="",
        default_planning_mode=None,
    )

    assert observed["interactive"] is True
    assert resolved.llm_base_url == "https://gateway.example/v1"


def test_resolve_profile_runtime_core_provider_change_uses_new_provider_default_base_url(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Provider change should not keep stale base URL from previous provider by default."""

    settings = Settings(root_dir=tmp_path)
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_provider",
        lambda **kwargs: "openai-codex",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_model",
        lambda **kwargs: "gpt-5.4",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_thinking_level",
        lambda **kwargs: "medium",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.profile_resolution.resolve_proxy",
        lambda **kwargs: ("none", ""),
    )

    def _fake_resolve_text(*, value, interactive, prompt, default, lang):
        del value, prompt, lang
        observed["interactive"] = interactive
        observed["default"] = default
        return default

    monkeypatch.setattr("afkbot.services.setup.profile_resolution.resolve_text", _fake_resolve_text)

    resolved = resolve_profile_runtime_core(
        interactive=True,
        lang=PromptLanguage.EN,
        settings=settings,
        provider_value="openai-codex",
        model_value=None,
        thinking_level_value=None,
        thinking_level_prompt="Reasoning effort",
        base_url_value=None,
        base_url_prompt="Provider base URL",
        custom_interface_value=None,
        proxy_type_value=None,
        proxy_url_value=None,
        planning_mode_value=None,
        default_provider="openrouter",
        default_model="gpt-5.4",
        default_thinking_level="medium",
        default_base_url="https://openrouter.ai/api/v1",
        default_custom_interface="openai",
        default_proxy_type="none",
        default_proxy_url="",
        default_planning_mode=None,
    )

    assert observed == {
        "interactive": False,
        "default": "https://chatgpt.com/backend-api/codex",
    }
    assert resolved.llm_base_url == "https://chatgpt.com/backend-api/codex"
