"""Tests for managed runtime reload hooks."""

from __future__ import annotations

from pytest import CaptureFixture, MonkeyPatch

from afkbot.cli.managed_runtime import (
    ManagedRuntimeReloadResult,
    reload_install_managed_runtime,
    reload_install_managed_runtime_notice,
)


def test_reload_install_managed_runtime_requires_manual_restart_without_service(
    monkeypatch: MonkeyPatch,
) -> None:
    """Without a managed host service file, runtime reload should fall back to manual restart."""

    monkeypatch.setattr(
        "afkbot.cli.managed_runtime.ensure_managed_runtime_service",
        lambda settings, *, start: ManagedRuntimeReloadResult(
            status="manual_restart_required",
            reason="Changes were saved. Restart `afk start` to apply them locally.",
        ),
    )

    # Act
    result = reload_install_managed_runtime()

    # Assert
    assert result.status == "manual_restart_required"
    assert "afk start" in (result.reason or "")


def test_reload_install_managed_runtime_restarts_managed_host_service(
    monkeypatch: MonkeyPatch,
) -> None:
    """When the managed service exists, runtime reload should restart it."""

    monkeypatch.setattr(
        "afkbot.cli.managed_runtime.ensure_managed_runtime_service",
        lambda settings, *, start: ManagedRuntimeReloadResult(status="installed"),
    )

    # Act
    result = reload_install_managed_runtime()

    # Assert
    assert result.status == "installed"


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
