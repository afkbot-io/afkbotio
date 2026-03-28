"""Tests for `afk upgrade` CLI commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pytest import MonkeyPatch
from typer.testing import CliRunner

from afkbot.cli.main import app
from afkbot.settings import get_settings
from afkbot.services.upgrade import UpgradeApplyReport


def _prepare_root(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AFKBOT_ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("AFKBOT_DB_URL", f"sqlite+aiosqlite:///{tmp_path / 'afkbot.db'}")
    monkeypatch.delenv("AFKBOT_SKIP_SETUP_GUARD", raising=False)
    get_settings.cache_clear()


def test_upgrade_apply_is_available_before_setup(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Upgrade apply should stay callable even when setup marker is absent."""

    _prepare_root(tmp_path, monkeypatch)
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade", "apply", "--quiet"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert isinstance(payload["steps"], list)


def test_upgrade_apply_uses_one_event_loop_for_service_lifecycle(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Apply and shutdown should share one event loop across the SQLite runtime."""

    _prepare_root(tmp_path, monkeypatch)
    loop_ids: list[tuple[str, int]] = []

    class _FakeUpgradeService:
        def __init__(self, settings) -> None:  # type: ignore[no-untyped-def]
            self._settings = settings

        async def apply(self) -> UpgradeApplyReport:
            del self._settings
            loop_ids.append(("apply", id(asyncio.get_running_loop())))
            return UpgradeApplyReport(changed=False, steps=())

        async def shutdown(self) -> None:
            loop_ids.append(("shutdown", id(asyncio.get_running_loop())))

    monkeypatch.setattr("afkbot.cli.commands.upgrade.UpgradeService", _FakeUpgradeService)
    runner = CliRunner()

    result = runner.invoke(app, ["upgrade", "apply", "--quiet"])

    assert result.exit_code == 0
    assert loop_ids == [
        ("apply", loop_ids[0][1]),
        ("shutdown", loop_ids[0][1]),
    ]
