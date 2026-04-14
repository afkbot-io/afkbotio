"""Tests for managed runtime service installation helpers."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from pytest import MonkeyPatch

from afkbot.services.managed_runtime_service import (
    ensure_managed_runtime_service,
    remove_managed_runtime_service,
    restart_managed_runtime_service,
    stop_managed_runtime_service,
)
from afkbot.settings import Settings


def test_ensure_managed_runtime_service_prefers_linux_system_unit_when_available(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Linux installs should prefer a system-level systemd unit when writable."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    launcher_path = tmp_path / "bin" / "afk"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher_path.chmod(0o755)
    service_path = tmp_path / "systemd-system" / "afkbot-tester.service"
    user_service_path = tmp_path / "systemd-user" / "afkbot.service"
    legacy_service_path = tmp_path / "systemd-system" / "afkbot.service"
    calls: list[list[str]] = []

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._preferred_systemd_system_service_path",
        lambda: service_path,
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service._SYSTEMD_USER_SERVICE_PATH", user_service_path)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH",
        legacy_service_path,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.shutil.which",
        lambda value: (
            "/bin/systemctl"
            if value == "systemctl"
            else "/usr/bin/install"
            if value == "install"
            else None
        ),
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._resolve_current_afk_launcher_path",
        lambda: launcher_path,
    )

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[0] == "/usr/bin/install":
            source_path = Path(command[-2])
            target_path = Path(command[-1])
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = ensure_managed_runtime_service(settings, start=True)

    assert result.status == "installed"
    assert result.kind == "systemd-system"
    assert result.path == service_path
    assert service_path.exists()
    rendered = service_path.read_text(encoding="utf-8")
    assert "afkbot-managed-runtime-service" in rendered
    assert "WantedBy=multi-user.target" in rendered
    assert '\\"completed\\"' in rendered
    assert str(settings.root_dir) in rendered
    assert str(settings.setup_state_path) in rendered
    assert str(launcher_path) in rendered
    assert calls[:4] == [
        [
            "/usr/bin/install",
            "-D",
            "-m",
            "644",
            calls[0][-2],
            str(service_path),
        ],
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "afkbot-tester.service"],
        ["systemctl", "restart", "afkbot-tester.service"],
    ]
    assert calls[4:] == [
        ["systemctl", "is-active", "afkbot-tester.service"],
        ["systemctl", "is-enabled", "afkbot-tester.service"],
    ]


def test_ensure_managed_runtime_service_falls_back_to_linux_user_unit_when_system_service_is_unavailable(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Linux installs should keep working with a user unit when system service install is unavailable."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    launcher_path = tmp_path / "bin" / "afk"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher_path.chmod(0o755)
    service_path = tmp_path / "systemd-user" / "afkbot.service"
    system_service_path = tmp_path / "systemd-system" / "afkbot.service"
    legacy_service_path = tmp_path / "systemd-system" / "afkbot-legacy.service"
    wants_path = service_path.parent / "default.target.wants" / service_path.name
    calls: list[list[str]] = []

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.shutil.which",
        lambda value: "/bin/systemctl" if value == "systemctl" else None,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._preferred_systemd_system_service_path",
        lambda: system_service_path,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH",
        legacy_service_path,
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service._SYSTEMD_USER_SERVICE_PATH", service_path)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._resolve_current_afk_launcher_path",
        lambda: launcher_path,
    )

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = ensure_managed_runtime_service(settings, start=True)

    assert result.status == "installed"
    assert result.kind == "systemd-user"
    assert service_path.exists()
    assert wants_path.is_symlink()
    rendered = service_path.read_text(encoding="utf-8")
    assert '\\"completed\\"' in rendered
    assert "Using a user-level systemd service" in (result.reason or "")
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "restart", "afkbot.service"],
        ["systemctl", "--user", "is-active", "afkbot.service"],
        ["systemctl", "--user", "is-enabled", "afkbot.service"],
    ]


def test_ensure_managed_runtime_service_reports_linger_guidance_when_user_bus_is_unavailable(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Linux user-service install should keep the unit on disk and explain the linger requirement."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    launcher_path = tmp_path / "bin" / "afk"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher_path.chmod(0o755)
    service_path = tmp_path / "systemd-user" / "afkbot.service"
    system_service_path = tmp_path / "systemd-system" / "afkbot.service"
    legacy_service_path = tmp_path / "systemd-system" / "afkbot-legacy.service"
    wants_path = service_path.parent / "default.target.wants" / service_path.name

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.shutil.which",
        lambda value: "/bin/systemctl" if value == "systemctl" else None,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._preferred_systemd_system_service_path",
        lambda: system_service_path,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH",
        legacy_service_path,
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service._SYSTEMD_USER_SERVICE_PATH", service_path)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._resolve_current_afk_launcher_path",
        lambda: launcher_path,
    )

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="Failed to connect to bus: No medium found",
        )

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = ensure_managed_runtime_service(settings, start=True)

    assert result.status == "manual_restart_required"
    assert "loginctl enable-linger" in (result.reason or "")
    assert service_path.exists()
    assert wants_path.is_symlink()


def test_ensure_managed_runtime_service_does_not_bootstrap_launchd_when_start_is_false(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Launchd install without start should only write the plist and avoid loading the agent."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    launcher_path = tmp_path / "bin" / "afk"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher_path.chmod(0o755)
    service_path = tmp_path / "LaunchAgents" / "io.afkbot.afkbot.plist"
    calls: list[list[str]] = []

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("afkbot.services.managed_runtime_service._LAUNCHD_SERVICE_PATH", service_path)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._resolve_current_afk_launcher_path",
        lambda: launcher_path,
    )

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = ensure_managed_runtime_service(settings, start=False)

    assert result.status == "installed"
    assert result.kind == "launchd"
    assert result.path == service_path
    assert service_path.exists()
    assert calls == [["launchctl", "print", f"gui/{os.getuid()}/io.afkbot.afkbot"]]


def test_ensure_managed_runtime_service_refuses_to_replace_unmanaged_system_unit(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Managed installs should leave unrelated systemd units untouched and fall back safely."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    launcher_path = tmp_path / "bin" / "afk"
    launcher_path.parent.mkdir(parents=True, exist_ok=True)
    launcher_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher_path.chmod(0o755)
    service_path = tmp_path / "systemd-system" / "afkbot-tester.service"
    user_service_path = tmp_path / "systemd-user" / "afkbot.service"
    legacy_service_path = tmp_path / "systemd-system" / "afkbot.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("[Unit]\nDescription=manual\n", encoding="utf-8")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._preferred_systemd_system_service_path",
        lambda: service_path,
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service._SYSTEMD_USER_SERVICE_PATH", user_service_path)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH",
        legacy_service_path,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.shutil.which",
        lambda value: "/bin/systemctl" if value == "systemctl" else "/usr/bin/install",
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._resolve_current_afk_launcher_path",
        lambda: launcher_path,
    )

    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )

    result = ensure_managed_runtime_service(settings, start=False)

    assert result.status == "installed"
    assert result.kind == "systemd-user"
    assert "Using a user-level systemd service" in (result.reason or "")
    assert service_path.read_text(encoding="utf-8") == "[Unit]\nDescription=manual\n"
    assert user_service_path.exists()


def test_stop_launchd_service_reports_domain_failure(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Launchd stop should return a failure when no known domain accepts bootout."""

    service_path = tmp_path / "LaunchAgents" / "io.afkbot.afkbot.plist"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("<!-- afkbot-managed-runtime-service -->\n", encoding="utf-8")
    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Darwin")
    monkeypatch.setattr("afkbot.services.managed_runtime_service._LAUNCHD_SERVICE_PATH", service_path)
    monkeypatch.setattr("afkbot.services.managed_runtime_service.setup_is_complete", lambda settings: False)

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 3, stdout="", stderr="Could not find service")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = stop_managed_runtime_service(settings)

    assert result.status == "failed"
    assert "launchctl bootout failed" in (result.reason or "")


def test_restart_managed_runtime_service_fails_when_health_never_recovers(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Restart should fail when the service manager accepts it but AFKBOT never becomes healthy."""

    service_path = tmp_path / "systemd-user" / "afkbot.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("# afkbot-managed-runtime-service\n", encoding="utf-8")
    system_service_path = tmp_path / "systemd-system" / "afkbot.service"
    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    calls: list[list[str]] = []

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._preferred_systemd_system_service_path",
        lambda: system_service_path,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH",
        tmp_path / "systemd-system" / "afkbot-legacy.service",
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service._SYSTEMD_USER_SERVICE_PATH", service_path)
    monkeypatch.setattr("afkbot.services.managed_runtime_service.setup_is_complete", lambda settings: True)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.inspect_managed_runtime_service",
        lambda settings: SimpleNamespace(
            running=False,
            manager_state="active=failed, enabled=enabled",
            runtime_ok=False,
            api_ok=False,
        ),
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.time.monotonic",
        iter([0.0, 0.0, 31.0]).__next__,
    )

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = restart_managed_runtime_service(settings)

    assert result.status == "failed"
    assert "did not become healthy" in (result.reason or "")
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "restart", "afkbot.service"],
    ]


def test_restart_managed_runtime_service_requires_active_manager_state(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Restart should not report success when health responds but systemd never reaches active."""

    service_path = tmp_path / "systemd-user" / "afkbot.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("# afkbot-managed-runtime-service\n", encoding="utf-8")
    system_service_path = tmp_path / "systemd-system" / "afkbot.service"
    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._preferred_systemd_system_service_path",
        lambda: system_service_path,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH",
        tmp_path / "systemd-system" / "afkbot-legacy.service",
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service._SYSTEMD_USER_SERVICE_PATH", service_path)
    monkeypatch.setattr("afkbot.services.managed_runtime_service.setup_is_complete", lambda settings: True)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.inspect_managed_runtime_service",
        lambda settings: SimpleNamespace(
            installed=True,
            kind="systemd-user",
            running=True,
            manager_state="active=activating, enabled=enabled",
            runtime_ok=True,
            api_ok=True,
        ),
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.time.monotonic",
        iter([0.0, 0.0, 31.0]).__next__,
    )

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = restart_managed_runtime_service(settings)

    assert result.status == "failed"
    assert "manager_state=active=activating, enabled=enabled" in (result.reason or "")


def test_restart_managed_runtime_service_requires_completed_setup(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Restart should fail clearly until full `afk setup` has completed."""

    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")
    monkeypatch.setattr("afkbot.services.managed_runtime_service.setup_is_complete", lambda settings: False)

    result = restart_managed_runtime_service(settings)

    assert result.status == "failed"
    assert "Run `afk setup`" in (result.reason or "")


def test_stop_managed_runtime_service_fails_when_one_endpoint_is_still_healthy(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Stop should fail when only part of the AFKBOT stack remains reachable."""

    service_path = tmp_path / "systemd-user" / "afkbot.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("# afkbot-managed-runtime-service\n", encoding="utf-8")
    system_service_path = tmp_path / "systemd-system" / "afkbot.service"
    settings = Settings(root_dir=tmp_path, db_url=f"sqlite+aiosqlite:///{tmp_path / 'service.db'}")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._preferred_systemd_system_service_path",
        lambda: system_service_path,
    )
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH",
        tmp_path / "systemd-system" / "afkbot-legacy.service",
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service._SYSTEMD_USER_SERVICE_PATH", service_path)
    monkeypatch.setattr("afkbot.services.managed_runtime_service.setup_is_complete", lambda settings: True)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.inspect_managed_runtime_service",
        lambda settings: SimpleNamespace(
            installed=True,
            kind="systemd-user",
            running=False,
            manager_state="active=inactive, enabled=enabled",
            runtime_ok=False,
            api_ok=True,
        ),
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service.time.sleep", lambda seconds: None)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service.time.monotonic",
        iter([0.0, 0.0, 16.0]).__next__,
    )

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = stop_managed_runtime_service(settings)

    assert result.status == "failed"
    assert "daemon is still responding" in (result.reason or "")


def test_remove_managed_runtime_service_removes_linux_system_service(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    """Linux uninstall should remove managed system-level units when present."""

    service_path = tmp_path / "systemd-system" / "afkbot-tester.service"
    user_service_path = tmp_path / "systemd-user" / "afkbot.service"
    legacy_service_path = tmp_path / "systemd-system" / "afkbot.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text("# afkbot-managed-runtime-service\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr("afkbot.services.managed_runtime_service.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._preferred_systemd_system_service_path",
        lambda: service_path,
    )
    monkeypatch.setattr("afkbot.services.managed_runtime_service._SYSTEMD_USER_SERVICE_PATH", user_service_path)
    monkeypatch.setattr(
        "afkbot.services.managed_runtime_service._SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH",
        legacy_service_path,
    )

    def _fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:2] == ["rm", "-f"]:
            Path(command[-1]).unlink(missing_ok=True)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("afkbot.services.managed_runtime_service.subprocess.run", _fake_run)

    result = remove_managed_runtime_service()

    assert result.status == "removed"
    assert result.kind == "systemd-system"
    assert not service_path.exists()
    assert calls == [
        ["systemctl", "disable", "--now", "afkbot-tester.service"],
        ["rm", "-f", str(service_path)],
        ["systemctl", "daemon-reload"],
    ]
