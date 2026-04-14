"""Tests for settings resolution."""

from __future__ import annotations

from pathlib import Path
import sys

import pytest
from pytest import MonkeyPatch

from afkbot.runtime_defaults import DEFAULT_LLM_MAX_ITERATIONS
from afkbot.services.setup.runtime_store import write_runtime_config, write_runtime_secrets
from afkbot.settings import Settings, get_settings


def test_settings_paths(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Settings should resolve root-dependent directories."""

    # Arrange
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.delenv("AFKBOT_DB_URL", raising=False)
    # Ensure local `.env` values do not leak into this unit test run.
    monkeypatch.setenv("AFKBOT_OPENROUTER_API_KEY", "")
    get_settings.cache_clear()

    # Act
    settings = get_settings()

    # Assert
    assert settings.app_dir == tmp_path
    assert settings.bootstrap_dir == tmp_path / "afkbot/bootstrap"
    assert settings.skills_dir == tmp_path / "afkbot/skills"
    assert settings.profiles_dir == tmp_path / "profiles"
    assert settings.db_url == f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}"
    assert settings.tool_workspace_dir == tmp_path
    assert "AGENTS.md" in settings.bootstrap_files
    assert settings.tool_timeout_default_sec == 15
    assert settings.tool_timeout_max_sec == 120
    assert settings.agent_tool_parallel_max_concurrent == 4
    assert settings.subagent_timeout_grace_sec == 5
    assert settings.subagent_wait_default_sec == 5
    assert settings.subagent_wait_max_sec == 300
    assert settings.subagent_task_ttl_sec == 3600
    assert settings.credentials_master_keys is None
    assert settings.enabled_tool_plugins == (
        "app_list",
        "app_run",
        "automation_create",
        "automation_list",
        "automation_get",
        "automation_update",
        "automation_delete",
        "task_board",
        "task_block",
        "task_comment_add",
        "task_comment_list",
        "task_create",
        "task_delegate",
        "task_dependency_add",
        "task_dependency_list",
        "task_dependency_remove",
        "task_event_list",
        "task_flow_create",
        "task_flow_list",
        "task_flow_get",
        "task_list",
        "task_maintenance_sweep",
        "task_get",
        "task_inbox",
        "task_review_list",
        "task_review_approve",
        "task_review_request_changes",
        "task_run_list",
        "task_run_get",
        "task_stale_list",
        "task_update",
        "credentials_create",
        "credentials_update",
        "credentials_delete",
        "credentials_list",
        "credentials_request",
        "file_list",
        "file_read",
        "file_write",
        "file_edit",
        "file_search",
        "diffs_render",
        "bash_exec",
        "browser_control",
        "debug_echo",
        "http_request",
        "web_search",
        "web_fetch",
        "memory_upsert",
        "memory_search",
        "memory_delete",
        "memory_digest",
        "memory_list",
        "memory_promote",
        "mcp_profile_list",
        "mcp_profile_get",
        "mcp_profile_upsert",
        "mcp_profile_delete",
        "mcp_profile_validate",
        "skill_profile_list",
        "skill_profile_get",
        "skill_profile_upsert",
        "skill_profile_delete",
        "skill_marketplace_list",
        "skill_marketplace_search",
        "skill_marketplace_install",
        "session_job_run",
        "subagent_run",
        "subagent_wait",
        "subagent_result",
        "subagent_profile_list",
        "subagent_profile_get",
        "subagent_profile_upsert",
        "subagent_profile_delete",
    )
    assert settings.llm_provider == "openrouter"
    assert settings.llm_model == "minimax/minimax-m2.5"
    assert settings.llm_api_key is None
    assert settings.llm_base_url is None
    assert settings.openrouter_api_key is None
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.openai_api_key is None
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.openai_codex_api_key is None
    assert settings.openai_codex_base_url == "https://chatgpt.com/backend-api/codex"
    assert settings.claude_api_key is None
    assert settings.claude_base_url == "https://api.anthropic.com/v1"
    assert settings.moonshot_api_key is None
    assert settings.moonshot_base_url == "https://api.moonshot.ai/v1"
    assert settings.deepseek_api_key is None
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.xai_api_key is None
    assert settings.xai_base_url == "https://api.x.ai/v1"
    assert settings.qwen_api_key is None
    assert settings.qwen_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert settings.minimax_portal_api_key is None
    assert settings.minimax_portal_base_url == "https://api.minimax.io/v1"
    assert settings.github_copilot_api_key is None
    assert settings.github_copilot_base_url == "https://api.individual.githubcopilot.com"
    assert settings.brave_api_key is None
    assert settings.llm_proxy_type == "none"
    assert settings.llm_proxy_url is None
    assert settings.llm_request_timeout_sec == 1800.0
    assert settings.llm_shared_request_max_parallel == 4
    assert settings.llm_execution_budget_low_sec == 900.0
    assert settings.llm_execution_budget_medium_sec == 1800.0
    assert settings.llm_execution_budget_high_sec == 3600.0
    assert settings.llm_execution_budget_very_high_sec == 7200.0
    assert settings.llm_thinking_level == "medium"
    assert settings.chat_planning_mode == "auto"
    assert settings.runtime_host == "127.0.0.1"
    assert settings.runtime_port == 46339
    assert settings.runtime_queue_max_size == 100
    assert settings.runtime_worker_count == 4
    assert settings.runtime_cron_interval_sec == 60.0
    assert settings.runtime_cron_max_due_per_tick == 32
    assert settings.runtime_shutdown_timeout_sec == 10.0
    assert settings.runtime_read_timeout_sec == 5.0
    assert settings.runtime_max_header_bytes == 16384
    assert settings.runtime_max_body_bytes == 262144
    assert settings.taskflow_runtime_poll_interval_sec == 5.0
    assert settings.taskflow_runtime_maintenance_batch_size == 32
    assert settings.taskflow_runtime_claim_ttl_sec == 900
    assert settings.browser_headless is True
    assert settings.diffs_artifact_ttl_sec == 86400
    assert settings.nginx_enabled is False
    assert settings.nginx_port == 18080
    assert settings.public_runtime_url is None
    assert settings.public_chat_api_url is None
    assert settings.nginx_config_path is None
    assert settings.diffs_artifacts_dir == tmp_path / "profiles/.system/artifacts/diffs"
    assert settings.setup_state_path == tmp_path / "profiles/.system/setup_state.json"
    assert settings.llm_max_iterations == DEFAULT_LLM_MAX_ITERATIONS
    assert settings.llm_history_turns == 8
    assert settings.memory_retention_days == 180
    assert settings.memory_max_items_per_profile == 5000
    assert settings.memory_gc_batch_size == 500
    assert settings.memory_auto_search_scope_mode == "auto"
    assert settings.memory_auto_search_include_global is True
    assert settings.memory_auto_search_chat_limit == 3
    assert settings.memory_auto_search_global_limit == 2
    assert settings.memory_global_fallback_enabled is True
    assert settings.memory_auto_save_scope_mode == "auto"
    assert settings.memory_auto_promote_enabled is False
    assert settings.memory_auto_save_kinds == (
        "fact",
        "preference",
        "decision",
        "task",
        "risk",
        "note",
    )
    assert settings.session_compaction_enabled is False
    assert settings.session_compaction_trigger_turns == 12
    assert settings.session_compaction_keep_recent_turns == 6
    assert settings.session_compaction_max_chars == 4000
    assert settings.session_compaction_prune_raw_turns is False
    get_settings.cache_clear()


def test_settings_installed_tool_layout_uses_user_runtime_root_and_package_assets(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Installed tool mode should separate runtime state from bundled application assets."""

    package_root = tmp_path / "tool-install"
    monkeypatch.delenv("AFKBOT_ROOT_DIR", raising=False)
    monkeypatch.delenv("AFKBOT_APP_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("afkbot.settings._package_root", lambda: package_root)
    get_settings.cache_clear()

    settings = Settings()

    if sys.platform == "darwin":
        expected_root = tmp_path / "Library" / "Application Support" / "AFKBOT"
    elif sys.platform == "win32":
        expected_root = Path.home() / "AppData" / "Local" / "AFKBOT"
    else:
        expected_root = tmp_path / ".local" / "share" / "afkbot"

    assert settings.root_dir == expected_root
    assert settings.app_dir == package_root
    assert settings.bootstrap_dir == package_root / "afkbot/bootstrap"
    assert settings.skills_dir == package_root / "afkbot/skills"
    assert settings.profiles_dir == expected_root / "profiles"


def test_settings_installed_tool_explicit_runtime_root_keeps_package_assets(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Installed tool mode should not redirect packaged assets when only root_dir is overridden."""

    package_root = tmp_path / "tool-install"
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr("afkbot.settings._package_root", lambda: package_root)

    settings = Settings(root_dir=runtime_root)

    assert settings.root_dir == runtime_root
    assert settings.app_dir == package_root
    assert settings.bootstrap_dir == package_root / "afkbot/bootstrap"
    assert settings.skills_dir == package_root / "afkbot/skills"
    assert settings.profiles_dir == runtime_root / "profiles"


def test_settings_runtime_limits_validation() -> None:
    """Settings should reject invalid runtime numeric limits."""

    with pytest.raises(ValueError):
        Settings(runtime_queue_max_size=0)
    with pytest.raises(ValueError):
        Settings(runtime_worker_count=0)
    with pytest.raises(ValueError):
        Settings(agent_tool_parallel_max_concurrent=0)
    with pytest.raises(ValueError):
        Settings(runtime_read_timeout_sec=0.0)
    with pytest.raises(ValueError):
        Settings(taskflow_runtime_poll_interval_sec=0.0)
    with pytest.raises(ValueError):
        Settings(taskflow_runtime_maintenance_batch_size=0)
    with pytest.raises(ValueError):
        Settings(taskflow_runtime_claim_ttl_sec=0)
    with pytest.raises(ValueError):
        Settings(llm_request_timeout_sec=0.0)
    with pytest.raises(ValueError):
        Settings(llm_request_timeout_sec=1800.1)
    with pytest.raises(ValueError):
        Settings(runtime_shutdown_timeout_sec=-1.0)
    with pytest.raises(ValueError):
        Settings(llm_history_turns=-1)
    with pytest.raises(ValueError):
        Settings(memory_retention_days=0)
    with pytest.raises(ValueError):
        Settings(memory_max_items_per_profile=0)
    with pytest.raises(ValueError):
        Settings(memory_gc_batch_size=0)
    with pytest.raises(ValueError):
        Settings(session_compaction_trigger_turns=0)
    with pytest.raises(ValueError):
        Settings(session_compaction_keep_recent_turns=0)
    with pytest.raises(ValueError):
        Settings(session_compaction_max_chars=0)


def test_settings_runtime_store_respects_allowlists(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Runtime config/secrets should only apply to their respective allowlisted fields."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.delenv("AFKBOT_RUNTIME_PORT", raising=False)
    monkeypatch.delenv("AFKBOT_LLM_API_KEY", raising=False)
    get_settings.cache_clear()

    seeded = Settings()
    write_runtime_config(
        seeded,
        config={
            "runtime_port": 19000,
            "browser_headless": False,
            "public_runtime_url": "https://app.example.com",
            "public_chat_api_url": "https://chat.example.com",
            "nginx_config_path": "/etc/nginx/conf.d/afkbot.conf",
            "openai_api_key": "config-secret-must-be-ignored",
            "root_dir": "/tmp/unsafe-root-override",
        },
    )
    write_runtime_secrets(
        seeded,
        secrets={
            "llm_api_key": "runtime-secret-key",
            "runtime_port": "17000",
        },
    )

    settings = get_settings()
    assert settings.runtime_port == 19000
    assert settings.browser_headless is False
    assert settings.public_runtime_url == "https://app.example.com"
    assert settings.public_chat_api_url == "https://chat.example.com"
    assert settings.nginx_config_path == "/etc/nginx/conf.d/afkbot.conf"
    assert settings.llm_api_key == "runtime-secret-key"
    assert settings.openai_api_key is None


def test_settings_runtime_store_applies_browser_backend_overrides(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runtime config should apply persisted browser backend and CDP URL overrides."""

    # Arrange
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.delenv("AFKBOT_BROWSER_BACKEND", raising=False)
    monkeypatch.delenv("AFKBOT_BROWSER_CDP_URL", raising=False)
    get_settings.cache_clear()
    seeded = Settings()
    write_runtime_config(
        seeded,
        config={
            "browser_backend": "lightpanda_cdp",
            "browser_cdp_url": "http://127.0.0.1:9222",
        },
    )

    # Act
    settings = get_settings()

    # Assert
    assert settings.browser_backend == "lightpanda_cdp"
    assert settings.browser_cdp_url == "http://127.0.0.1:9222"
    get_settings.cache_clear()
    assert settings.root_dir == tmp_path
    get_settings.cache_clear()


def test_settings_env_values_override_runtime_store(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Non-empty env values must take precedence over persisted runtime store values."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_RUNTIME_PORT", "18001")
    monkeypatch.setenv("AFKBOT_LLM_API_KEY", "env-key")
    get_settings.cache_clear()

    seeded = Settings()
    write_runtime_config(seeded, config={"runtime_port": 19000})
    write_runtime_secrets(seeded, secrets={"llm_api_key": "runtime-secret-key"})

    settings = get_settings()
    assert settings.runtime_port == 18001
    assert settings.llm_api_key == "env-key"
    get_settings.cache_clear()


def test_settings_normalizes_brave_api_key(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Brave API key should be loaded from env and normalized for empty values."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_BRAVE_API_KEY", "  brave-key-1  ")
    get_settings.cache_clear()
    assert get_settings().brave_api_key == "brave-key-1"

    monkeypatch.setenv("AFKBOT_BRAVE_API_KEY", "   ")
    get_settings.cache_clear()
    assert get_settings().brave_api_key is None
    get_settings.cache_clear()


def test_settings_tool_workspace_override(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Tool workspace should support absolute and relative overrides."""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_TOOL_WORKSPACE_ROOT", "/tmp/afkbot-tool-space")
    get_settings.cache_clear()
    assert get_settings().tool_workspace_dir == Path("/tmp/afkbot-tool-space").resolve(strict=False)

    monkeypatch.setenv("AFKBOT_TOOL_WORKSPACE_ROOT", "userdata")
    get_settings.cache_clear()
    assert get_settings().tool_workspace_dir == tmp_path / "userdata"

    monkeypatch.setenv("AFKBOT_TOOL_WORKSPACE_ROOT", "   ")
    get_settings.cache_clear()
    assert get_settings().tool_workspace_dir == tmp_path
    get_settings.cache_clear()


def test_settings_normalizes_browser_cdp_url_shorthand(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Browser CDP URL env values should accept host:port shorthand and normalize it."""

    # Arrange
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_BROWSER_CDP_URL", "127.0.0.1:9222")
    get_settings.cache_clear()

    # Act
    settings = get_settings()

    # Assert
    assert settings.browser_cdp_url == "http://127.0.0.1:9222"
    get_settings.cache_clear()
