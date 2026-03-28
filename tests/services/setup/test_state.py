"""Tests for setup bootstrap-state detection helpers."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.setup.state import manual_local_runtime_is_ready, platform_is_bootstrapped
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
