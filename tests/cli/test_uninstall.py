"""Tests for local uninstall command."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.setup.runtime_store import write_runtime_config
from afkbot.services.setup.state import SetupStateSnapshot, write_setup_state
from afkbot.settings import get_settings


def test_uninstall_clears_local_state(tmp_path: Path, monkeypatch) -> None:
    """Uninstall should remove setup state, runtime store, profiles, and local SQLite DB."""

    # Arrange
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    (tmp_path / "profiles/default").mkdir(parents=True, exist_ok=True)
    (tmp_path / "afkbot.db").write_text("db", encoding="utf-8")
    write_runtime_config(
        settings,
        config={
            "db_url": settings.db_url,
            "runtime_host": "127.0.0.1",
            "runtime_port": 8080,
        },
    )
    write_setup_state(
        settings,
        SetupStateSnapshot(
            env_file=str(tmp_path / ".unused"),
            db_url=settings.db_url,
            llm_provider="openai",
            llm_model="gpt-4o-mini",
            llm_thinking_level="medium",
            llm_proxy_type="none",
            llm_proxy_configured=False,
            credentials_master_keys_configured=False,
            runtime_host="127.0.0.1",
            runtime_port=8080,
            nginx_enabled=False,
            nginx_port=18080,
            public_runtime_url="",
            public_chat_api_url="",
            prompt_language="en",
            update_notices_enabled=True,
            policy_setup_mode="recommended",
            policy_enabled=True,
            policy_preset="medium",
            policy_confirmation_mode="destructive_files",
            policy_capabilities=(),
            policy_allowed_tools=(),
            policy_file_access_mode="read_write",
            policy_allowed_directories=(),
            policy_network_mode="unrestricted",
            policy_network_allowlist=("*",),
        ),
    )
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["uninstall", "--yes"])

    # Assert
    assert result.exit_code == 0
    assert not (tmp_path / "profiles").exists()
    assert not (tmp_path / "afkbot.db").exists()
    assert not settings.runtime_config_path.exists()
    assert not settings.setup_state_path.exists()


def test_uninstall_requires_confirmation_in_non_interactive_mode(tmp_path: Path, monkeypatch) -> None:
    """Uninstall should fail closed without --yes in non-interactive mode."""

    # Arrange
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    get_settings.cache_clear()
    runner = CliRunner()

    # Act
    result = runner.invoke(app, ["uninstall"])

    # Assert
    assert result.exit_code == 2
    assert "confirmation required" in result.output
