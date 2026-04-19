"""Tests for `afk auth` CLI commands."""

from __future__ import annotations

from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.services.setup.runtime_store import read_runtime_config, read_runtime_secrets
from afkbot.settings import get_settings


def test_auth_setup_persists_runtime_policy_and_secrets(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk auth setup` should persist config and encrypted secrets into the runtime store."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "auth",
            "setup",
            "--yes",
            "--username",
            "operator",
            "--password",
            "correct-horse-battery",
            "--session-ttl-sec",
            "3600",
            "--idle-ttl-sec",
            "900",
            "--login-rate-limit-window-sec",
            "600",
            "--login-rate-limit-max-attempts",
            "4",
            "--lockout-sec",
            "600",
            "--protected-plugin-id",
            "afkbotui",
            "--protected-plugin-id",
            "demo",
        ],
    )

    assert result.exit_code == 0
    config = read_runtime_config(get_settings())
    secrets = read_runtime_secrets(get_settings())
    assert config["ui_auth_mode"] == "password"
    assert config["ui_auth_username"] == "operator"
    assert config["ui_auth_protected_plugin_ids"] == ["afkbotui", "demo"]
    assert config["ui_auth_session_ttl_sec"] == 3600
    assert config["ui_auth_idle_ttl_sec"] == 900
    assert secrets["ui_auth_password_hash"].startswith("scrypt$")
    assert secrets["ui_auth_cookie_key"]
    get_settings.cache_clear()


def test_auth_disable_clears_runtime_policy_and_secrets(
    tmp_path,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """`afk auth disable` should remove the persisted UI auth keys."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    get_settings.cache_clear()
    runner = CliRunner()

    setup_result = runner.invoke(
        app,
        [
            "auth",
            "setup",
            "--yes",
            "--username",
            "operator",
            "--password",
            "correct-horse-battery",
            "--protected-plugin-id",
            "afkbotui",
        ],
    )
    assert setup_result.exit_code == 0

    disable_result = runner.invoke(app, ["auth", "disable", "--yes"])

    assert disable_result.exit_code == 0
    config = read_runtime_config(get_settings())
    secrets = read_runtime_secrets(get_settings())
    assert "ui_auth_mode" not in config
    assert "ui_auth_username" not in config
    assert "ui_auth_password_hash" not in secrets
    assert "ui_auth_cookie_key" not in secrets
    get_settings.cache_clear()

