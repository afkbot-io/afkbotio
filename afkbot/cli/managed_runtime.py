"""Best-effort reload hook for local managed runtime mutations."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys

import typer

from afkbot.settings import Settings, get_settings

_HOST_SERVICE_MARKER = "afkbot-managed-runtime-service"
_SYSTEMD_SERVICE_NAME = "afkbot.service"
_SYSTEMD_SERVICE_PATH = Path("/etc/systemd/system/afkbot.service")
_LAUNCHD_SERVICE_NAME = "io.afkbot.afkbot"
_LAUNCHD_SERVICE_PATH = Path.home() / "Library/LaunchAgents" / f"{_LAUNCHD_SERVICE_NAME}.plist"


@dataclass(frozen=True)
class ManagedRuntimeReloadResult:
    """Describe how the managed runtime reload attempt finished."""

    status: str
    reason: str | None = None


def reload_install_managed_runtime(settings: Settings | None = None) -> ManagedRuntimeReloadResult:
    """Restart the managed host runtime when config changes require a reload."""

    _ = settings or get_settings()
    return _restart_host_runtime()


def reload_install_managed_runtime_notice(settings: Settings | None = None) -> None:
    """Emit one warning only when the managed runtime reload failed."""

    try:
        result = reload_install_managed_runtime(settings)
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(
            "Changes were saved, but AFKBOT could not reload the managed runtime automatically "
            f"({exc.__class__.__name__}: {exc}). Restart the app manually if needed.",
            err=True,
        )
        return
    if result.status not in {"failed", "manual_restart_required"} or not result.reason:
        return
    typer.echo(result.reason, err=True)


def _restart_host_runtime() -> ManagedRuntimeReloadResult:
    if sys.platform.startswith("linux"):
        return _restart_systemd_service()
    if sys.platform == "darwin":
        return _restart_launchd_service()
    return ManagedRuntimeReloadResult(
        status="manual_restart_required",
        reason="Changes were saved. Restart `afk start` to apply them locally.",
    )


def _restart_systemd_service() -> ManagedRuntimeReloadResult:
    if not _managed_service_file_present(_SYSTEMD_SERVICE_PATH):
        return ManagedRuntimeReloadResult(
            status="manual_restart_required",
            reason="Changes were saved. Restart `afk start` to apply them locally.",
        )

    commands: list[list[str]] = [["systemctl", "restart", _SYSTEMD_SERVICE_NAME]]
    sudo_path = shutil.which("sudo")
    if os.geteuid() != 0 and sudo_path is not None:
        commands.append(
            [
                sudo_path,
                "-n",
                "env",
                f"PATH={os.environ.get('PATH', '')}",
                "systemctl",
                "restart",
                _SYSTEMD_SERVICE_NAME,
            ]
        )

    error = ""
    for command in commands:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            error = str(exc)
            continue
        if completed.returncode == 0:
            return ManagedRuntimeReloadResult(status="restarted")
        error = _completed_process_error(completed)
    return ManagedRuntimeReloadResult(
        status="failed",
        reason=(
            "Changes were saved, but AFKBOT could not restart the managed host service."
            f" {error or 'Restart it manually.'}"
        ),
    )


def _restart_launchd_service() -> ManagedRuntimeReloadResult:
    if not _managed_service_file_present(_LAUNCHD_SERVICE_PATH):
        return ManagedRuntimeReloadResult(
            status="manual_restart_required",
            reason="Changes were saved. Restart `afk start` to apply them locally.",
        )

    uid_value = str(os.getuid())
    error = ""
    for domain in (f"gui/{uid_value}", f"user/{uid_value}"):
        try:
            subprocess.run(
                ["launchctl", "bootout", domain, str(_LAUNCHD_SERVICE_PATH)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            completed = subprocess.run(
                ["launchctl", "bootstrap", domain, str(_LAUNCHD_SERVICE_PATH)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            error = str(exc)
            continue
        if completed.returncode == 0:
            subprocess.run(
                ["launchctl", "kickstart", "-k", f"{domain}/{_LAUNCHD_SERVICE_NAME}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return ManagedRuntimeReloadResult(status="restarted")
        error = _completed_process_error(completed)
    return ManagedRuntimeReloadResult(
        status="failed",
        reason=(
            "Changes were saved, but AFKBOT could not restart the managed host service."
            f" {error or 'Restart it manually.'}"
        ),
    )


def _managed_service_file_present(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return _HOST_SERVICE_MARKER in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _completed_process_error(result: subprocess.CompletedProcess[str]) -> str:
    stderr = (result.stderr or "").strip()
    if stderr:
        return stderr.splitlines()[0]
    stdout = (result.stdout or "").strip()
    if stdout:
        return stdout.splitlines()[0]
    return f"exit code {result.returncode}"
