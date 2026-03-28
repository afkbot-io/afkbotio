"""Tests for managed Lightpanda runtime helpers."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.lightpanda_runtime import (
    get_lightpanda_managed_status,
    start_lightpanda_runtime,
    stop_lightpanda_runtime,
)
from afkbot.settings import Settings


def test_get_lightpanda_managed_status_clears_stale_pid_for_other_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Managed status should not trust pid files that point at another process."""

    # Arrange
    settings = Settings(root_dir=tmp_path, browser_cdp_url="http://127.0.0.1:9222")
    pid_path = tmp_path / "tmp" / "lightpanda" / "lightpanda.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4321\n", encoding="utf-8")
    monkeypatch.setattr(
        "afkbot.services.lightpanda_runtime._pid_matches_expected_binary",
        lambda pid, *, binary_path: False,
    )

    # Act
    status = get_lightpanda_managed_status(settings)

    # Assert
    assert status.running is False
    assert status.pid is None
    assert pid_path.exists() is False


def test_start_lightpanda_runtime_rejects_occupied_port(monkeypatch, tmp_path: Path) -> None:
    """Managed start should fail before launching a new process when the target port is busy."""

    # Arrange
    settings = Settings(root_dir=tmp_path, browser_cdp_url="http://127.0.0.1:9222")
    binary_path = tmp_path / "bin" / "lightpanda"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
    binary_path.chmod(0o755)
    monkeypatch.setattr(
        "afkbot.services.lightpanda_runtime._socket_is_ready",
        lambda *, host, port: True,
    )

    def _unexpected_popen(*args, **kwargs):  # type: ignore[no-untyped-def]
        _ = args, kwargs
        raise AssertionError("subprocess.Popen must not be called when the port is already occupied")

    monkeypatch.setattr("afkbot.services.lightpanda_runtime.subprocess.Popen", _unexpected_popen)

    # Act
    result = start_lightpanda_runtime(settings=settings)

    # Assert
    assert result.ok is False
    assert result.error_code == "lightpanda_port_in_use"
    assert "already listening" in result.reason


def test_start_lightpanda_runtime_requires_explicit_cdp_url(tmp_path: Path) -> None:
    """Managed start should require an explicitly configured CDP URL."""

    # Arrange
    settings = Settings(root_dir=tmp_path, browser_cdp_url=None)

    # Act
    result = start_lightpanda_runtime(settings=settings)

    # Assert
    assert result.ok is False
    assert result.error_code == "browser_cdp_url_missing"
    assert "afk browser cdp-url" in result.reason


def test_start_lightpanda_runtime_starts_managed_process(monkeypatch, tmp_path: Path) -> None:
    """Managed start should launch Lightpanda and persist its pid when the endpoint is free."""

    # Arrange
    settings = Settings(root_dir=tmp_path, browser_cdp_url="http://127.0.0.1:9222")
    binary_path = tmp_path / "bin" / "lightpanda"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
    binary_path.chmod(0o755)
    seen: dict[str, object] = {}

    class _FakeProcess:
        pid = 9876

        def poll(self) -> None:
            return None

    def _fake_popen(command, **kwargs):  # type: ignore[no-untyped-def]
        seen["command"] = command
        seen["env"] = kwargs.get("env")
        return _FakeProcess()

    monkeypatch.setattr("afkbot.services.lightpanda_runtime.subprocess.Popen", _fake_popen)
    monkeypatch.setattr(
        "afkbot.services.lightpanda_runtime._socket_is_ready",
        lambda *, host, port: False,
    )
    monkeypatch.setattr(
        "afkbot.services.lightpanda_runtime._wait_for_runtime_ready",
        lambda **kwargs: True,
    )

    # Act
    result = start_lightpanda_runtime(settings=settings)

    # Assert
    assert result.ok is True
    assert result.running is True
    assert result.pid == 9876
    assert seen["command"] == [
        str(binary_path),
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "9222",
    ]
    assert seen["env"]["LIGHTPANDA_DISABLE_TELEMETRY"] == "true"
    assert (tmp_path / "tmp" / "lightpanda" / "lightpanda.pid").read_text(encoding="utf-8").strip() == "9876"


def test_stop_lightpanda_runtime_keeps_pid_file_when_stop_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Managed stop should keep pid tracking when process termination fails."""

    # Arrange
    settings = Settings(root_dir=tmp_path, browser_cdp_url="http://127.0.0.1:9222")
    binary_path = tmp_path / "bin" / "lightpanda"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_text("#!/bin/sh\n", encoding="utf-8")
    binary_path.chmod(0o755)
    pid_path = tmp_path / "tmp" / "lightpanda" / "lightpanda.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("4321\n", encoding="utf-8")
    monkeypatch.setattr(
        "afkbot.services.lightpanda_runtime._pid_matches_expected_binary",
        lambda pid, *, binary_path: True,
    )
    monkeypatch.setattr(
        "afkbot.services.lightpanda_runtime._terminate_pid",
        lambda pid, *, timeout_sec: False,
    )

    # Act
    result = stop_lightpanda_runtime(settings=settings)

    # Assert
    assert result.ok is False
    assert result.error_code == "lightpanda_stop_failed"
    assert result.running is True
    assert result.pid == 4321
    assert pid_path.read_text(encoding="utf-8").strip() == "4321"
