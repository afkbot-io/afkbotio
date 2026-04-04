"""Shared helpers for setup CLI tests."""

from __future__ import annotations

import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings


def prepare_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Prepare one isolated root dir and baseline env vars for setup tests."""

    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'test-setup.db'}")
    monkeypatch.setenv("AFKBOT_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("AFKBOT_LLM_MODEL", "minimax/minimax-m2.5")
    monkeypatch.setenv("AFKBOT_LLM_API_KEY", "seed-key")
    monkeypatch.setenv("AFKBOT_LLM_BASE_URL", "")
    monkeypatch.setenv("AFKBOT_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("AFKBOT_LLM_PROXY_TYPE", "none")
    monkeypatch.setenv("AFKBOT_RUNTIME_HOST", "127.0.0.1")
    monkeypatch.setenv("AFKBOT_RUNTIME_PORT", "46339")
    monkeypatch.setenv("AFKBOT_NGINX_ENABLED", "0")
    monkeypatch.setenv("AFKBOT_NGINX_PORT", "18080")
    get_settings.cache_clear()


def bootstrap_platform(_tmp_path: Path) -> dict[str, object]:
    """Seed platform runtime config via internal bootstrap-only CLI path."""

    # Arrange
    runner = CliRunner()

    # Act
    result = runner.invoke(
        app,
        [
            "setup",
            "--bootstrap-only",
            "--yes",
            "--accept-risk",
            "--skip-llm-token-verify",
        ],
    )

    # Assert
    if result.exit_code != 0:
        raise AssertionError(result.output)
    return json.loads(result.stdout.strip())
