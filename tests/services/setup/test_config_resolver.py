"""Tests for setup config resolver behavior."""

from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from afkbot.cli.presentation.prompt_i18n import PromptLanguage
from afkbot.services.llm.provider_catalog import LLMProviderId
from afkbot.services.policy import PolicyCapabilityId
from afkbot.services.setup.config_resolver import collect_setup_config
from afkbot.services.setup.defaults import recommended_policy_capabilities
from afkbot.services.setup.profile_resolution import (
    ResolvedProfilePolicyInputs,
    ResolvedProfileRuntimeCore,
)
from afkbot.settings import Settings


def test_collect_setup_config_recommended_mode_uses_recommended_capabilities(
    tmp_path: Path,
) -> None:
    """Recommended setup should keep its curated capability set and local SQLite runtime."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'install-config-resolver.db'}",
    )

    # Act
    config = collect_setup_config(
        settings=settings,
        defaults={
            "AFKBOT_DB_URL": f"sqlite+aiosqlite:///{tmp_path / 'configured.db'}",
            "AFKBOT_LLM_PROVIDER": "openrouter",
            "AFKBOT_LLM_MODEL": "minimax/minimax-m2.5",
            "AFKBOT_OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "AFKBOT_LLM_API_KEY": "seed-key",
            "AFKBOT_CREDENTIALS_MASTER_KEYS": "seed-master-key",
            "AFKBOT_POLICY_SETUP_MODE": "recommended",
        },
        env_file=tmp_path / ".unused",
        interactive=False,
        lang=PromptLanguage.EN,
        llm_provider=None,
        chat_model=None,
        thinking_level=None,
        llm_api_key_file=None,
        llm_base_url=None,
        custom_interface=None,
        skip_llm_token_verify=True,
        llm_proxy_type=None,
        llm_proxy_url=None,
        runtime_host=None,
        runtime_port=None,
        nginx_enabled=None,
        nginx_port=None,
        nginx_runtime_host=None,
        nginx_runtime_https=None,
        nginx_api_host=None,
        nginx_api_https=None,
        certbot_email=None,
        policy_enabled=None,
        policy_preset=None,
        policy_capability=(),
        policy_file_access_mode=None,
        policy_workspace_scope=None,
        policy_network_host=(),
        auto_install_deps=None,
    )

    # Assert
    expected_capabilities = tuple(
        capability.value
        for capability in (
            PolicyCapabilityId.MEMORY,
            PolicyCapabilityId.CREDENTIALS,
            PolicyCapabilityId.SUBAGENTS,
            PolicyCapabilityId.AUTOMATION,
            PolicyCapabilityId.TASKFLOW,
            PolicyCapabilityId.HTTP,
            PolicyCapabilityId.WEB,
            PolicyCapabilityId.BROWSER,
            PolicyCapabilityId.SKILLS,
            PolicyCapabilityId.APPS,
            PolicyCapabilityId.MCP,
        )
    )
    assert config.db_url == f"sqlite+aiosqlite:///{tmp_path / 'configured.db'}"
    assert config.policy_setup_mode == "recommended"
    assert config.policy_preset == "medium"
    assert config.policy_capabilities == expected_capabilities
    assert config.policy_capabilities == recommended_policy_capabilities()
    assert config.llm_thinking_level == "medium"
    assert config.default_profile_runtime_config.llm_provider == "openrouter"
    assert config.default_profile_runtime_config.llm_model == "minimax/minimax-m2.5"
    assert config.default_profile_runtime_config.llm_thinking_level == "medium"
    assert config.nginx_runtime_host == ""
    assert config.nginx_api_host == ""
    assert config.public_runtime_url == ""
    assert config.public_chat_api_url == ""
    assert config.auto_install_deps is True


def test_recommended_policy_capabilities_keep_browser_while_dropping_high_risk_tools() -> None:
    """Recommended capability helper should keep browser while excluding files and shell."""

    # Arrange
    expected_capabilities = tuple(
        capability.value
        for capability in (
            PolicyCapabilityId.MEMORY,
            PolicyCapabilityId.CREDENTIALS,
            PolicyCapabilityId.SUBAGENTS,
            PolicyCapabilityId.AUTOMATION,
            PolicyCapabilityId.TASKFLOW,
            PolicyCapabilityId.HTTP,
            PolicyCapabilityId.WEB,
            PolicyCapabilityId.BROWSER,
            PolicyCapabilityId.SKILLS,
            PolicyCapabilityId.APPS,
            PolicyCapabilityId.MCP,
        )
    )

    # Act
    capabilities = recommended_policy_capabilities()

    # Assert
    assert capabilities == expected_capabilities
    assert PolicyCapabilityId.FILES.value not in capabilities
    assert PolicyCapabilityId.SHELL.value not in capabilities
    assert PolicyCapabilityId.BROWSER.value in capabilities
    assert PolicyCapabilityId.DEBUG.value not in capabilities


def test_collect_setup_config_supports_explicit_workspace_scope_override(tmp_path: Path) -> None:
    """Custom policy answers should persist the selected workspace scope."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'install-config-resolver.db'}",
    )

    # Act
    config = collect_setup_config(
        settings=settings,
        defaults={
            "AFKBOT_LLM_PROVIDER": "openai",
            "AFKBOT_LLM_MODEL": "gpt-4o-mini",
            "AFKBOT_LLM_API_KEY": "seed-key",
            "AFKBOT_CREDENTIALS_MASTER_KEYS": "seed-master-key",
        },
        env_file=tmp_path / ".unused",
        interactive=False,
        lang=PromptLanguage.EN,
        llm_provider=None,
        chat_model=None,
        thinking_level=None,
        llm_api_key_file=None,
        llm_base_url=None,
        custom_interface=None,
        skip_llm_token_verify=True,
        llm_proxy_type=None,
        llm_proxy_url=None,
        runtime_host=None,
        runtime_port=None,
        nginx_enabled=None,
        nginx_port=None,
        nginx_runtime_host=None,
        nginx_runtime_https=None,
        nginx_api_host=None,
        nginx_api_https=None,
        certbot_email=None,
        policy_enabled=True,
        policy_preset="medium",
        policy_capability=("files",),
        policy_file_access_mode="read_only",
        policy_workspace_scope="project_only",
        policy_network_host=(),
        auto_install_deps=None,
    )

    # Assert
    assert config.policy_workspace_scope_mode == "project_only"
    assert config.policy_allowed_directories == (str(tmp_path.resolve(strict=False)),)


def test_collect_setup_config_does_not_rerun_policy_setup_mode_when_policy_already_resolved(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Second config-assembly pass should not rerun high-level policy selection."""

    # Arrange
    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'install-config-resolver.db'}",
    )
    runtime_core = ResolvedProfileRuntimeCore(
        provider_id=LLMProviderId.OPENAI,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_base_url="https://api.openai.com/v1",
        custom_interface="openai",
        llm_proxy_type="none",
        llm_proxy_url="",
        llm_thinking_level="medium",
        chat_planning_mode=None,
    )
    resolved_policy = ResolvedProfilePolicyInputs(
        enabled=True,
        preset="simple",
        capabilities=("files",),
        file_access_mode="read_write",
        workspace_scope_mode="full_system",
        allowed_directories=(),
        network_mode="unrestricted",
        network_allowlist=("*",),
    )

    def _fail(**_: object) -> str:
        raise AssertionError("resolve_policy_setup_mode should not run when policy is already resolved")

    monkeypatch.setattr(
        "afkbot.services.setup.config_resolver.resolve_policy_setup_mode",
        _fail,
    )

    # Act
    config = collect_setup_config(
        settings=settings,
        defaults={
            "AFKBOT_LLM_PROVIDER": "openai",
            "AFKBOT_LLM_MODEL": "gpt-4o-mini",
            "AFKBOT_LLM_API_KEY": "seed-key",
            "AFKBOT_CREDENTIALS_MASTER_KEYS": "seed-master-key",
        },
        env_file=tmp_path / ".unused",
        interactive=True,
        lang=PromptLanguage.EN,
        llm_provider=None,
        chat_model=None,
        thinking_level=None,
        llm_api_key_file=None,
        llm_base_url=None,
        custom_interface=None,
        skip_llm_token_verify=True,
        llm_proxy_type=None,
        llm_proxy_url=None,
        runtime_host=None,
        runtime_port=None,
        nginx_enabled=None,
        nginx_port=None,
        nginx_runtime_host=None,
        nginx_runtime_https=None,
        nginx_api_host=None,
        nginx_api_https=None,
        certbot_email=None,
        policy_enabled=None,
        policy_preset=None,
        policy_capability=(),
        policy_file_access_mode=None,
        policy_workspace_scope=None,
        policy_network_host=(),
        auto_install_deps=None,
        resolved_runtime_core=runtime_core,
        resolved_policy_inputs=resolved_policy,
        resolved_api_key="seed-key",
        profile_setup_only=True,
    )

    # Assert
    assert config.policy_preset == "simple"
    assert config.policy_setup_mode == "custom"


def test_collect_setup_config_uses_auto_selected_exotic_runtime_port_when_unconfigured(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """First-time setup should use the resolved exotic runtime port instead of the legacy 8080 default."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'install-config-resolver.db'}",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.config_resolver.resolve_default_runtime_port",
        lambda *, settings, host, runtime_config: 46341,
    )

    config = collect_setup_config(
        settings=settings,
        defaults={
            "AFKBOT_LLM_PROVIDER": "openrouter",
            "AFKBOT_LLM_MODEL": "minimax/minimax-m2.5",
            "AFKBOT_OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "AFKBOT_LLM_API_KEY": "seed-key",
            "AFKBOT_CREDENTIALS_MASTER_KEYS": "seed-master-key",
        },
        env_file=tmp_path / ".unused",
        interactive=False,
        lang=PromptLanguage.EN,
        llm_provider=None,
        chat_model=None,
        thinking_level=None,
        llm_api_key_file=None,
        llm_base_url=None,
        custom_interface=None,
        skip_llm_token_verify=True,
        llm_proxy_type=None,
        llm_proxy_url=None,
        runtime_host=None,
        runtime_port=None,
        nginx_enabled=None,
        nginx_port=None,
        nginx_runtime_host=None,
        nginx_runtime_https=None,
        nginx_api_host=None,
        nginx_api_https=None,
        certbot_email=None,
        policy_enabled=None,
        policy_preset=None,
        policy_capability=(),
        policy_file_access_mode=None,
        policy_workspace_scope=None,
        policy_network_host=(),
        auto_install_deps=None,
        platform_seed_only=True,
    )

    assert config.runtime_port == 46341


def test_collect_setup_config_prompts_for_update_notices_during_public_setup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Interactive public setup should ask about chat-time update notices."""

    settings = Settings(
        root_dir=tmp_path,
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'install-config-resolver.db'}",
    )
    monkeypatch.setattr(
        "afkbot.services.setup.config_resolver.prompt_update_notices_enabled",
        lambda *, default, lang: False,
    )

    config = collect_setup_config(
        settings=settings,
        defaults={
            "AFKBOT_LLM_PROVIDER": "openai",
            "AFKBOT_LLM_MODEL": "gpt-4o-mini",
            "AFKBOT_LLM_API_KEY": "seed-key",
            "AFKBOT_CREDENTIALS_MASTER_KEYS": "seed-master-key",
        },
        env_file=tmp_path / ".unused",
        interactive=True,
        lang=PromptLanguage.EN,
        llm_provider=None,
        chat_model=None,
        thinking_level=None,
        llm_api_key_file=None,
        llm_base_url=None,
        custom_interface=None,
        skip_llm_token_verify=True,
        llm_proxy_type=None,
        llm_proxy_url=None,
        runtime_host=None,
        runtime_port=None,
        nginx_enabled=None,
        nginx_port=None,
        nginx_runtime_host=None,
        nginx_runtime_https=None,
        nginx_api_host=None,
        nginx_api_https=None,
        certbot_email=None,
        policy_enabled=None,
        policy_preset=None,
        policy_capability=(),
        policy_file_access_mode=None,
        policy_workspace_scope=None,
        policy_network_host=(),
        auto_install_deps=None,
        resolved_api_key="seed-key",
        profile_setup_only=True,
    )

    assert config.update_notices_enabled is False
