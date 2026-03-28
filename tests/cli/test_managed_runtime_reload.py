"""Tests for managed runtime reload hooks."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pytest import CaptureFixture, MonkeyPatch

from afkbot.cli.managed_runtime import (
    reload_install_managed_runtime,
    reload_install_managed_runtime_notice,
)


def test_reload_install_managed_runtime_requires_manual_restart_without_service(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Without a managed host service file, runtime reload should fall back to manual restart."""

    # Arrange
    monkeypatch.setattr("afkbot.cli.managed_runtime.sys.platform", "linux")
    monkeypatch.setattr(
        "afkbot.cli.managed_runtime._managed_service_file_present",
        lambda path: False,
    )

    # Act
    result = reload_install_managed_runtime()

    # Assert
    assert result.status == "manual_restart_required"
    assert "afk start" in (result.reason or "")


def test_reload_install_managed_runtime_restarts_managed_host_service(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """When the managed service exists, runtime reload should restart it."""

    # Arrange
    monkeypatch.setattr("afkbot.cli.managed_runtime.sys.platform", "linux")
    monkeypatch.setattr(
        "afkbot.cli.managed_runtime._managed_service_file_present",
        lambda path: True,
    )
    monkeypatch.setattr("afkbot.cli.managed_runtime.os.geteuid", lambda: 0)
    captured: dict[str, object] = {}

    def _fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.cli.managed_runtime.subprocess.run", _fake_run)

    # Act
    result = reload_install_managed_runtime()

    # Assert
    assert result.status == "restarted"
    assert captured["cmd"] == ["systemctl", "restart", "afkbot.service"]


def test_reload_install_managed_runtime_notice_swallow_unexpected_errors(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    """Best-effort notice should warn instead of crashing on unexpected reload errors."""

    # Arrange
    monkeypatch.setattr(
        "afkbot.cli.managed_runtime.reload_install_managed_runtime",
        lambda settings=None: (_ for _ in ()).throw(PermissionError("denied")),
    )

    # Act
    reload_install_managed_runtime_notice()

    # Assert
    err = capsys.readouterr().err
    assert "Changes were saved" in err
    assert "PermissionError: denied" in err
