"""Tests for profile-scoped runtime config resolution."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.profile_runtime import ProfileRuntimeConfig
from afkbot.services.profile_runtime.runtime_config import ProfileRuntimeConfigService
from afkbot.services.profile_runtime.runtime_secrets import ProfileRuntimeSecretsService
from afkbot.settings import Settings


def test_profile_runtime_config_roundtrip(tmp_path: Path) -> None:
    """Service should persist and reload one profile runtime config."""

    settings = Settings(root_dir=tmp_path)
    service = ProfileRuntimeConfigService(settings)
    config = ProfileRuntimeConfig(
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        llm_base_url="https://api.openai.com/v1",
        llm_proxy_type="http",
        llm_proxy_url="http://127.0.0.1:8888",
        llm_thinking_level="high",
        llm_history_turns=16,
        chat_planning_mode="on",
        chat_secret_guard_enabled=True,
        enabled_tool_plugins=("debug_echo", "file_read"),
        memory_auto_search_enabled=True,
        memory_auto_search_limit=4,
        memory_auto_context_item_chars=320,
        memory_auto_save_enabled=True,
        memory_auto_save_max_chars=1200,
        session_compaction_enabled=True,
        session_compaction_trigger_turns=16,
        session_compaction_keep_recent_turns=8,
        session_compaction_max_chars=5000,
        session_compaction_prune_raw_turns=True,
    )

    path = service.write("analyst", config)
    loaded = service.load("analyst")

    assert path == tmp_path / "profiles/analyst/.system/agent_config.json"
    assert loaded == config
    assert (tmp_path / "profiles/analyst/.system").is_dir()
    assert (tmp_path / "profiles/analyst/bootstrap").is_dir()
    assert (tmp_path / "profiles/analyst/skills").is_dir()
    assert (tmp_path / "profiles/analyst/subagents").is_dir()


def test_profile_runtime_config_builds_effective_settings(tmp_path: Path) -> None:
    """Profile config should override runtime provider/model while inheriting unrelated settings."""

    settings = Settings(
        root_dir=tmp_path,
        llm_provider="openrouter",
        llm_model="minimax/minimax-m2.5",
        runtime_port=8080,
        enabled_tool_plugins=("debug_echo", "file_read", "file_write"),
    )
    service = ProfileRuntimeConfigService(settings)
    service.write(
        "analyst",
        ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_thinking_level="very_high",
            llm_history_turns=18,
            chat_planning_mode="on",
            chat_secret_guard_enabled=True,
            enabled_tool_plugins=("debug_echo", "file_read"),
            memory_auto_search_enabled=True,
            memory_auto_search_limit=4,
            session_compaction_enabled=True,
            session_compaction_trigger_turns=14,
            session_compaction_keep_recent_turns=7,
            session_compaction_prune_raw_turns=True,
        ),
    )

    resolved = service.build_effective_settings(profile_id="analyst", base_settings=settings)

    assert resolved.llm_provider == "openai"
    assert resolved.llm_model == "gpt-4o-mini"
    assert resolved.runtime_port == 8080
    assert resolved.llm_thinking_level == "very_high"
    assert resolved.llm_history_turns == 18
    assert resolved.chat_planning_mode == "on"
    assert resolved.chat_secret_guard_enabled is True
    assert resolved.enabled_tool_plugins == ("debug_echo", "file_read")
    assert resolved.memory_auto_search_enabled is True
    assert resolved.memory_auto_search_limit == 4
    assert resolved.session_compaction_enabled is True
    assert resolved.session_compaction_trigger_turns == 14
    assert resolved.session_compaction_keep_recent_turns == 7
    assert resolved.session_compaction_prune_raw_turns is True


def test_profile_runtime_config_applies_profile_local_provider_secrets(tmp_path: Path) -> None:
    """Effective profile settings should include encrypted profile-local provider keys."""

    settings = Settings(
        root_dir=tmp_path,
        llm_provider="openrouter",
        llm_model="minimax/minimax-m2.5",
        openai_api_key=None,
        llm_api_key="global-fallback-key",
    )
    config_service = ProfileRuntimeConfigService(settings)
    secrets_service = ProfileRuntimeSecretsService(settings)
    config_service.write(
        "analyst",
        ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        ),
    )
    secrets_service.write(
        "analyst",
        {
            "openai_api_key": "profile-openai-key",
        },
    )

    resolved = config_service.build_effective_settings(profile_id="analyst", base_settings=settings)
    summary = config_service.resolved_runtime(resolved)

    assert resolved.llm_provider == "openai"
    assert resolved.openai_api_key == "profile-openai-key"
    assert resolved.llm_api_key == "global-fallback-key"
    assert summary.provider_api_key_configured is True


def test_profile_runtime_config_applies_profile_local_brave_search_key(tmp_path: Path) -> None:
    """Profile-local Brave key should flow into effective settings and resolved summary."""

    settings = Settings(
        root_dir=tmp_path,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        brave_api_key=None,
    )
    config_service = ProfileRuntimeConfigService(settings)
    secrets_service = ProfileRuntimeSecretsService(settings)
    config_service.write(
        "searcher",
        ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        ),
    )
    secrets_service.write(
        "searcher",
        {
            "brave_api_key": "profile-brave-key",
        },
    )

    resolved = config_service.build_effective_settings(profile_id="searcher", base_settings=settings)
    summary = config_service.resolved_runtime(resolved)

    assert resolved.brave_api_key == "profile-brave-key"
    assert summary.brave_api_key_configured is True


def test_profile_runtime_resolved_runtime_projects_memory_kinds_without_runtime_memory_import(
    tmp_path: Path,
) -> None:
    """Resolved runtime should project memory kinds without requiring MemoryKind at runtime."""

    settings = Settings(
        root_dir=tmp_path,
        llm_provider="openai",
        llm_model="gpt-4o-mini",
        memory_auto_save_kinds=("fact", "decision"),
    )

    summary = ProfileRuntimeConfigService.resolved_runtime(settings)

    assert summary.memory_auto_save_kinds == ("fact", "decision")


def test_profile_runtime_config_can_repair_layout_explicitly(tmp_path: Path) -> None:
    """Explicit layout repair should recreate canonical profile directories."""

    settings = Settings(root_dir=tmp_path)
    service = ProfileRuntimeConfigService(settings)
    service.write(
        "legacy",
        ProfileRuntimeConfig(
            llm_provider="openai",
            llm_model="gpt-4o-mini",
        ),
    )

    for path in (
        tmp_path / "profiles/legacy/bootstrap",
        tmp_path / "profiles/legacy/skills",
        tmp_path / "profiles/legacy/subagents",
    ):
        path.rmdir()

    service.ensure_layout("legacy")

    assert (tmp_path / "profiles/legacy/bootstrap").is_dir()
    assert (tmp_path / "profiles/legacy/skills").is_dir()
    assert (tmp_path / "profiles/legacy/subagents").is_dir()


def test_profile_runtime_config_read_path_does_not_create_layout(tmp_path: Path) -> None:
    """Read-only resolution should not create profile layout implicitly."""

    settings = Settings(root_dir=tmp_path, llm_provider="openai", llm_model="gpt-4o-mini")
    service = ProfileRuntimeConfigService(settings)

    resolved = service.build_effective_settings(profile_id="reader", base_settings=settings)

    assert resolved.llm_provider == "openai"
    assert not (tmp_path / "profiles/reader").exists()
