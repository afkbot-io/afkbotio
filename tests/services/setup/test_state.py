"""Tests for setup bootstrap-state detection helpers."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.setup.state import (
    SetupStateSnapshot,
    build_setup_state_payload,
    manual_local_runtime_is_ready,
    platform_is_bootstrapped,
)
from afkbot.services.setup.runtime_store import write_runtime_config
from afkbot.settings import Settings


def test_platform_is_bootstrapped_accepts_persisted_runtime_config(tmp_path: Path) -> None:
    """Persisted runtime config should satisfy the setup bootstrap check."""

    # Arrange
    settings = Settings(root_dir=tmp_path)
    write_runtime_config(
        settings,
        config={
            "db_url": settings.db_url,
            "runtime_host": "127.0.0.1",
            "runtime_port": 8080,
        },
    )

    # Act
    result = platform_is_bootstrapped(settings)

    # Assert
    assert result is True


def test_manual_local_runtime_is_ready_accepts_source_checkout(tmp_path: Path) -> None:
    """Source checkout markers should satisfy the local readiness check."""

    # Arrange
    (tmp_path / "pyproject.toml").write_text("[project]\nname='afkbot'\n", encoding="utf-8")
    (tmp_path / "afkbot").mkdir()
    settings = Settings(root_dir=tmp_path)

    # Act
    result = manual_local_runtime_is_ready(settings)

    # Assert
    assert result is True


def test_setup_state_payload_includes_wizard_metadata() -> None:
    """New setup-state payloads should persist additive wizard metadata."""

    payload = build_setup_state_payload(
        SetupStateSnapshot(
            env_file=".unused",
            db_url="sqlite:///afkbot.db",
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_thinking_level="medium",
            llm_proxy_type="none",
            llm_proxy_configured=False,
            credentials_master_keys_configured=True,
            runtime_host="127.0.0.1",
            runtime_port=8080,
            nginx_enabled=False,
            nginx_port=80,
            public_runtime_url="",
            public_chat_api_url="",
            prompt_language="ru",
            update_notices_enabled=True,
            policy_setup_mode="recommended",
            policy_enabled=True,
            policy_preset="medium",
            policy_confirmation_mode="destructive_files",
            policy_capabilities=("memory", "taskflow"),
            policy_allowed_tools=(),
            policy_file_access_mode="none",
            policy_allowed_directories=(),
            policy_shell_sandbox_mode="disabled",
            policy_shell_allowed_commands=(),
            policy_network_mode="recommended",
            policy_network_allowlist=(),
            policy_workspace_scope_mode="profile_only",
            wizard_profile_scenario="taskflow_channel",
            wizard_setup_depth="quick",
            wizard_work_contexts=("channels",),
            wizard_actions=("reply", "channel_history", "taskflow", "memory"),
            wizard_isolation="no_files",
            wizard_confirmation="balanced",
            wizard_network="recommended",
            wizard_network_allowlist=("api.example.com",),
        )
    )

    assert payload["version"] == 2
    assert payload["config"]["wizard_schema_version"] == 1
    assert payload["config"]["wizard_profile_scenario"] == "taskflow_channel"
    assert payload["config"]["wizard_setup_depth"] == "quick"
    assert payload["config"]["wizard_work_contexts"] == ["channels"]
    assert payload["config"]["wizard_actions"] == ["reply", "channel_history", "taskflow", "memory"]
    assert payload["config"]["wizard_isolation"] == "no_files"
    assert payload["config"]["wizard_confirmation"] == "balanced"
    assert payload["config"]["wizard_network"] == "recommended"
    assert payload["config"]["wizard_network_allowlist"] == ["api.example.com"]
    assert payload["config"]["policy_workspace_scope_mode"] == "profile_only"
