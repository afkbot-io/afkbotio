"""Managed background-service install/reload helpers for AFKBOT."""

from __future__ import annotations

from dataclasses import dataclass
import grp
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess
import sys
import tempfile
import time

from afkbot.services.managed_install import resolve_managed_install_context
from afkbot.services.runtime_ports import (
    RuntimeStackProbe,
    probe_runtime_stack,
    resolve_default_runtime_port,
)
from afkbot.services.setup.runtime_store import read_runtime_config
from afkbot.services.setup.state import setup_is_complete
from afkbot.settings import Settings, get_settings

_HOST_SERVICE_MARKER = "afkbot-managed-runtime-service"
_SYSTEMD_SERVICE_NAME = "afkbot.service"
_SYSTEMD_USER_SERVICE_PATH = Path.home() / ".config" / "systemd" / "user" / _SYSTEMD_SERVICE_NAME
_SYSTEMD_SYSTEM_SERVICE_BASENAME = "afkbot"
_SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH = Path("/etc/systemd/system/afkbot.service")
_LAUNCHD_SERVICE_NAME = "io.afkbot.afkbot"
_LAUNCHD_SERVICE_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_SERVICE_NAME}.plist"
_START_WAIT_TIMEOUT_SEC = 60.0
_STOP_WAIT_TIMEOUT_SEC = 15.0
_POLL_INTERVAL_SEC = 1.0


@dataclass(frozen=True, slots=True)
class ManagedRuntimeServiceStatus:
    """Describe the currently installed AFKBOT background service."""

    installed: bool
    kind: str | None = None
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class ManagedRuntimeServiceResult:
    """Describe how one service-management operation finished."""

    status: str
    reason: str | None = None
    kind: str | None = None
    path: Path | None = None


@dataclass(frozen=True, slots=True)
class ManagedRuntimeServiceInspection:
    """Describe the current managed-service health and runtime reachability."""

    installed: bool
    running: bool
    kind: str | None = None
    path: Path | None = None
    manager_state: str | None = None
    runtime_url: str | None = None
    runtime_ok: bool = False
    api_url: str | None = None
    api_ok: bool = False


@dataclass(frozen=True, slots=True)
class _LinuxServiceAccount:
    user_name: str
    group_name: str
    home_path: Path


def describe_managed_runtime_service() -> ManagedRuntimeServiceStatus:
    """Return the detected managed-service installation, if present."""

    system_name = platform.system().lower()
    if system_name == "linux":
        system_path = _preferred_systemd_system_service_path()
        if _managed_service_file_present(system_path):
            return ManagedRuntimeServiceStatus(
                installed=True,
                kind="systemd-system",
                path=system_path,
            )
        if _legacy_systemd_system_service_belongs_to_current_user():
            return ManagedRuntimeServiceStatus(
                installed=True,
                kind="systemd-system",
                path=_SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH,
            )
        if _managed_service_file_present(_SYSTEMD_USER_SERVICE_PATH):
            return ManagedRuntimeServiceStatus(
                installed=True,
                kind="systemd-user",
                path=_SYSTEMD_USER_SERVICE_PATH,
            )
        return ManagedRuntimeServiceStatus(installed=False)
    if system_name == "darwin":
        if _managed_service_file_present(_LAUNCHD_SERVICE_PATH):
            return ManagedRuntimeServiceStatus(
                installed=True,
                kind="launchd",
                path=_LAUNCHD_SERVICE_PATH,
            )
        return ManagedRuntimeServiceStatus(installed=False)
    return ManagedRuntimeServiceStatus(installed=False)


def ensure_managed_runtime_service(
    settings: Settings,
    *,
    start: bool,
) -> ManagedRuntimeServiceResult:
    """Create or refresh the local managed-service definition for AFKBOT."""

    system_name = platform.system().lower()
    if system_name == "linux":
        if shutil.which("systemctl") is None:
            return ManagedRuntimeServiceResult(
                status="manual_restart_required",
                reason="systemctl is unavailable; run `afk start` manually on this host.",
            )
        result = _ensure_linux_service(settings=settings, start=start)
        finalized = _finalize_start_result(
            result,
            settings=settings,
            should_be_running=start and setup_is_complete(settings),
            action="start",
        )
        if finalized.status in {"installed", "restarted"} and finalized.kind == "systemd-system":
            finalized = _append_cleanup_notice(
                finalized,
                _cleanup_linux_secondary_services(active_system_path=finalized.path),
            )
        return finalized
    if system_name == "darwin":
        return _finalize_start_result(
            _ensure_launchd_service(settings=settings, start=start),
            settings=settings,
            should_be_running=start and setup_is_complete(settings),
            action="start",
        )
    return ManagedRuntimeServiceResult(
        status="manual_restart_required",
        reason="Managed AFKBOT services are only supported on Linux and macOS.",
    )


def start_managed_runtime_service(settings: Settings) -> ManagedRuntimeServiceResult:
    """Start or install the local managed AFKBOT service when setup is complete."""

    if not setup_is_complete(settings):
        return ManagedRuntimeServiceResult(
            status="failed",
            reason="Run `afk setup` before starting the managed AFKBOT daemon.",
        )
    return ensure_managed_runtime_service(settings, start=True)


def restart_managed_runtime_service(settings: Settings | None = None) -> ManagedRuntimeServiceResult:
    """Restart the detected managed AFKBOT service."""

    resolved_settings = settings or get_settings()
    if not setup_is_complete(resolved_settings):
        return ManagedRuntimeServiceResult(
            status="failed",
            reason="Run `afk setup` before restarting the managed AFKBOT daemon.",
        )
    status = describe_managed_runtime_service()
    if not status.installed or status.kind is None or status.path is None:
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason="Changes were saved. Restart `afk start` to apply them locally.",
        )
    if status.kind == "systemd-user":
        result = _restart_systemd_user_service(status.path)
    elif status.kind == "systemd-system":
        result = _restart_systemd_system_service(status.path)
    elif status.kind == "launchd":
        result = _restart_launchd_service(status.path)
    else:
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason="Changes were saved. Restart `afk start` to apply them locally.",
        )
    return _finalize_start_result(
        result,
        settings=resolved_settings,
        should_be_running=True,
        action="restart",
    )


def stop_managed_runtime_service(settings: Settings | None = None) -> ManagedRuntimeServiceResult:
    """Stop the detected managed AFKBOT service without uninstalling it."""

    status = describe_managed_runtime_service()
    if not status.installed or status.kind is None or status.path is None:
        return ManagedRuntimeServiceResult(status="not_installed")
    if status.kind == "systemd-user":
        result = _stop_systemd_user_service(status.path)
    elif status.kind == "systemd-system":
        result = _stop_systemd_system_service(status.path)
    elif status.kind == "launchd":
        result = _stop_launchd_service(status.path)
    else:
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason="Stop the AFKBOT daemon manually on this host.",
        )
    resolved_settings = settings or get_settings()
    return _finalize_stop_result(
        result,
        settings=resolved_settings,
        should_be_stopped=setup_is_complete(resolved_settings),
    )


def inspect_managed_runtime_service(settings: Settings) -> ManagedRuntimeServiceInspection:
    """Inspect the managed-service installation and live AFKBOT daemon health."""

    status = describe_managed_runtime_service()
    if not setup_is_complete(settings):
        return ManagedRuntimeServiceInspection(
            installed=status.installed,
            kind=status.kind,
            path=status.path,
            manager_state=_describe_manager_state(status),
            running=False,
        )
    stack_probe = _probe_runtime_stack(settings)
    return ManagedRuntimeServiceInspection(
        installed=status.installed,
        running=stack_probe.running,
        kind=status.kind,
        path=status.path,
        manager_state=_describe_manager_state(status),
        runtime_url=stack_probe.runtime.url,
        runtime_ok=stack_probe.runtime.ok,
        api_url=stack_probe.api.url,
        api_ok=stack_probe.api.ok,
    )


def remove_managed_runtime_service() -> ManagedRuntimeServiceResult:
    """Disable and delete the managed AFKBOT service when present."""

    status = describe_managed_runtime_service()
    if platform.system().lower() == "linux":
        results: list[ManagedRuntimeServiceResult] = []
        system_path = _preferred_systemd_system_service_path()
        if _managed_service_file_present(system_path):
            results.append(_remove_systemd_system_service(system_path))
        legacy_path = _SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH
        if legacy_path != system_path and _legacy_systemd_system_service_belongs_to_current_user():
            results.append(_remove_systemd_system_service(legacy_path))
        if _managed_service_file_present(_SYSTEMD_USER_SERVICE_PATH):
            results.append(_remove_systemd_user_service(_SYSTEMD_USER_SERVICE_PATH))
        if not results:
            return ManagedRuntimeServiceResult(status="not_installed")
        for result in results:
            if result.status == "failed":
                return result
        for result in results:
            if result.status == "manual_restart_required":
                return result
        return results[0]
    if not status.installed or status.kind is None or status.path is None:
        return ManagedRuntimeServiceResult(status="not_installed")
    if status.kind == "launchd":
        uid_value = str(os.getuid())
        for domain in (f"gui/{uid_value}", f"user/{uid_value}"):
            _run_command(["launchctl", "bootout", domain, str(status.path)], allow_failure=True)
        status.path.unlink(missing_ok=True)
        return ManagedRuntimeServiceResult(status="removed", kind=status.kind, path=status.path)
    return ManagedRuntimeServiceResult(
        status="manual_restart_required",
        reason="System-level AFKBOT service was left untouched.",
        kind=status.kind,
        path=status.path,
    )


def _ensure_linux_service(
    *,
    settings: Settings,
    start: bool,
) -> ManagedRuntimeServiceResult:
    existing = describe_managed_runtime_service()
    preferred_system_path = _preferred_systemd_system_service_path()
    had_system_service = _managed_service_file_present(preferred_system_path)
    system_result = _ensure_systemd_system_service(settings=settings, start=start)
    if system_result.status in {"installed", "restarted"}:
        return system_result
    if not had_system_service and _managed_service_file_present(preferred_system_path):
        _remove_systemd_system_service(preferred_system_path, allow_failure=True)
    if existing.kind == "systemd-system":
        return system_result

    user_result = _ensure_systemd_user_service(settings=settings, start=start)
    if user_result.status in {"installed", "restarted"}:
        fallback_reason = _linux_system_service_fallback_reason()
        reason = _append_reason(user_result.reason, fallback_reason)
        if reason == user_result.reason:
            return user_result
        return ManagedRuntimeServiceResult(
            status=user_result.status,
            reason=reason,
            kind=user_result.kind,
            path=user_result.path,
        )
    return _combine_linux_install_failures(system_result=system_result, user_result=user_result)


def _ensure_systemd_user_service(
    *,
    settings: Settings,
    start: bool,
) -> ManagedRuntimeServiceResult:
    launcher_path = _resolve_current_afk_launcher_path()
    if launcher_path is None:
        return ManagedRuntimeServiceResult(
            status="failed",
            reason="Failed to resolve the current AFKBOT launcher path.",
        )
    if _service_definition_is_unmanaged(_SYSTEMD_USER_SERVICE_PATH):
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason=(
                f"Refusing to replace unmanaged systemd user unit at {_SYSTEMD_USER_SERVICE_PATH}. "
                "Move that unit aside or manage the AFKBOT daemon manually on this host."
            ),
            kind="systemd-user",
            path=_SYSTEMD_USER_SERVICE_PATH,
        )
    rendered = _render_systemd_user_unit(settings=settings, launcher_path=launcher_path)
    _write_service_definition(_SYSTEMD_USER_SERVICE_PATH, rendered, mode=0o644)
    _ensure_systemd_user_enable_link(_SYSTEMD_USER_SERVICE_PATH)
    if not start:
        return ManagedRuntimeServiceResult(
            status="installed",
            kind="systemd-user",
            path=_SYSTEMD_USER_SERVICE_PATH,
        )
    daemon_reload = _run_command(["systemctl", "--user", "daemon-reload"], allow_failure=True)
    if daemon_reload.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason=_systemd_user_failure_reason(daemon_reload),
            kind="systemd-user",
            path=_SYSTEMD_USER_SERVICE_PATH,
        )
    service_result = _run_command(
        ["systemctl", "--user", "restart", _SYSTEMD_SERVICE_NAME],
        allow_failure=True,
    )
    if service_result.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason=_systemd_user_failure_reason(service_result),
            kind="systemd-user",
            path=_SYSTEMD_USER_SERVICE_PATH,
        )
    return ManagedRuntimeServiceResult(
        status="installed",
        kind="systemd-user",
        path=_SYSTEMD_USER_SERVICE_PATH,
    )


def _ensure_systemd_system_service(
    *,
    settings: Settings,
    start: bool,
) -> ManagedRuntimeServiceResult:
    launcher_path = _resolve_current_afk_launcher_path()
    if launcher_path is None:
        return ManagedRuntimeServiceResult(
            status="failed",
            reason="Failed to resolve the current AFKBOT launcher path.",
        )
    service_account = _resolve_linux_service_account()
    if service_account is None:
        return ManagedRuntimeServiceResult(
            status="failed",
            reason="Failed to resolve the current Linux user/group for the AFKBOT service.",
        )
    if (
        os.geteuid() == 0
        and str(os.getenv("SUDO_USER") or "").strip()
        and not str(os.getenv("AFKBOT_ROOT_DIR") or "").strip()
        and Path(settings.root_dir).expanduser().resolve(strict=False).parts[:2] == ("/", "root")
    ):
        return ManagedRuntimeServiceResult(
            status="failed",
            reason=(
                "This command is running under sudo with root-local AFKBOT state. "
                "Run `afk service install` as the target user, or preserve AFKBOT_ROOT_DIR when invoking sudo."
            ),
            kind="systemd-system",
            path=_preferred_systemd_system_service_path(),
        )
    rendered = _render_systemd_system_unit(
        settings=settings,
        launcher_path=launcher_path,
        user_name=service_account.user_name,
        group_name=service_account.group_name,
        home_path=service_account.home_path,
    )
    service_path = _preferred_systemd_system_service_path()
    service_name = _systemd_service_name_for_path(service_path)
    if _service_definition_is_unmanaged(service_path):
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason=(
                f"Refusing to replace unmanaged systemd system unit at {service_path}. "
                "Rename or remove that unit before enabling managed AFKBOT services here."
            ),
            kind="systemd-system",
            path=service_path,
        )
    write_result = _write_systemd_system_definition(service_path, rendered, mode=0o644)
    if write_result is not None and write_result.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason=_systemd_system_failure_reason(write_result, action="install"),
            kind="systemd-system",
            path=service_path,
        )
    daemon_reload = _run_root_command(["systemctl", "daemon-reload"])
    if daemon_reload.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason=_systemd_system_failure_reason(daemon_reload, action="reload"),
            kind="systemd-system",
            path=service_path,
        )
    enable_result = _run_root_command(["systemctl", "enable", service_name])
    if enable_result.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason=_systemd_system_failure_reason(enable_result, action="enable"),
            kind="systemd-system",
            path=service_path,
        )
    if start:
        restart_result = _run_root_command(["systemctl", "restart", service_name])
        if restart_result.returncode != 0:
            return ManagedRuntimeServiceResult(
                status="manual_restart_required",
                reason=_systemd_system_failure_reason(restart_result, action="restart"),
                kind="systemd-system",
                path=service_path,
            )
    return ManagedRuntimeServiceResult(
        status="installed",
        kind="systemd-system",
        path=service_path,
    )


def _ensure_launchd_service(
    *,
    settings: Settings,
    start: bool,
) -> ManagedRuntimeServiceResult:
    launcher_path = _resolve_current_afk_launcher_path()
    if launcher_path is None:
        return ManagedRuntimeServiceResult(
            status="failed",
            reason="Failed to resolve the current AFKBOT launcher path.",
        )
    rendered = _render_launchd_plist(settings=settings, launcher_path=launcher_path)
    _write_service_definition(_LAUNCHD_SERVICE_PATH, rendered, mode=0o644)
    if not start:
        return ManagedRuntimeServiceResult(
            status="installed",
            kind="launchd",
            path=_LAUNCHD_SERVICE_PATH,
        )
    uid_value = str(os.getuid())
    last_error = "launchctl could not load the AFKBOT agent automatically."
    for domain in (f"gui/{uid_value}", f"user/{uid_value}"):
        _run_command(["launchctl", "bootout", domain, str(_LAUNCHD_SERVICE_PATH)], allow_failure=True)
        bootstrap = _run_command(["launchctl", "bootstrap", domain, str(_LAUNCHD_SERVICE_PATH)])
        if bootstrap.returncode != 0:
            last_error = (
                f"launchctl bootstrap failed for domain {domain}: "
                f"{_completed_process_error(bootstrap)}"
            )
            continue
        _run_command(
            ["launchctl", "kickstart", "-k", f"{domain}/{_LAUNCHD_SERVICE_NAME}"],
            allow_failure=True,
        )
        return ManagedRuntimeServiceResult(
            status="installed",
            kind="launchd",
            path=_LAUNCHD_SERVICE_PATH,
        )
    return ManagedRuntimeServiceResult(
        status="manual_restart_required",
        reason=(
            f"{last_error} LaunchAgents start after user login; rerun `afk service start` "
            "from the target macOS user session or use `afk start` manually."
        ),
        kind="launchd",
        path=_LAUNCHD_SERVICE_PATH,
    )


def _restart_systemd_user_service(path: Path) -> ManagedRuntimeServiceResult:
    _ = path
    daemon_reload = _run_command(["systemctl", "--user", "daemon-reload"])
    if daemon_reload.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="failed",
            reason=_completed_process_error(daemon_reload),
            kind="systemd-user",
            path=_SYSTEMD_USER_SERVICE_PATH,
        )
    restart = _run_command(["systemctl", "--user", "restart", _SYSTEMD_SERVICE_NAME])
    if restart.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="failed",
            reason=_completed_process_error(restart),
            kind="systemd-user",
            path=_SYSTEMD_USER_SERVICE_PATH,
        )
    return ManagedRuntimeServiceResult(
        status="restarted",
        kind="systemd-user",
        path=_SYSTEMD_USER_SERVICE_PATH,
    )


def _restart_systemd_system_service(path: Path) -> ManagedRuntimeServiceResult:
    service_name = _systemd_service_name_for_path(path)
    commands = [
        ["systemctl", "daemon-reload"],
        ["systemctl", "restart", service_name],
    ]
    for command in commands:
        result = _run_root_command(command)
        if result.returncode == 0:
            continue
        return ManagedRuntimeServiceResult(
            status="failed",
            reason=_systemd_system_failure_reason(result, action="restart"),
            kind="systemd-system",
            path=path,
        )
    return ManagedRuntimeServiceResult(
        status="restarted",
        kind="systemd-system",
        path=path,
    )


def _stop_systemd_user_service(path: Path) -> ManagedRuntimeServiceResult:
    _ = path
    stop = _run_command(["systemctl", "--user", "stop", _SYSTEMD_SERVICE_NAME])
    if stop.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="failed",
            reason=_completed_process_error(stop),
            kind="systemd-user",
            path=_SYSTEMD_USER_SERVICE_PATH,
        )
    return ManagedRuntimeServiceResult(
        status="stopped",
        kind="systemd-user",
        path=_SYSTEMD_USER_SERVICE_PATH,
    )


def _stop_systemd_system_service(path: Path) -> ManagedRuntimeServiceResult:
    command = ["systemctl", "stop", _systemd_service_name_for_path(path)]
    result = _run_root_command(command)
    if result.returncode != 0:
        return ManagedRuntimeServiceResult(
            status="failed",
            reason=_systemd_system_failure_reason(result, action="stop"),
            kind="systemd-system",
            path=path,
        )
    return ManagedRuntimeServiceResult(
        status="stopped",
        kind="systemd-system",
        path=path,
    )


def _restart_launchd_service(path: Path) -> ManagedRuntimeServiceResult:
    uid_value = str(os.getuid())
    last_error = "launchctl could not restart the AFKBOT agent automatically."
    for domain in (f"gui/{uid_value}", f"user/{uid_value}"):
        _run_command(["launchctl", "bootout", domain, str(path)], allow_failure=True)
        bootstrap = _run_command(["launchctl", "bootstrap", domain, str(path)])
        if bootstrap.returncode != 0:
            last_error = (
                f"launchctl bootstrap failed for domain {domain}: "
                f"{_completed_process_error(bootstrap)}"
            )
            continue
        _run_command(
            ["launchctl", "kickstart", "-k", f"{domain}/{_LAUNCHD_SERVICE_NAME}"],
            allow_failure=True,
        )
        return ManagedRuntimeServiceResult(
            status="restarted",
            kind="launchd",
            path=path,
        )
    return ManagedRuntimeServiceResult(
        status="failed",
        reason=(
            f"{last_error} LaunchAgents start after user login; rerun `afk service restart` "
            "from the target macOS user session or use `afk start` manually."
        ),
        kind="launchd",
        path=path,
    )


def _stop_launchd_service(path: Path) -> ManagedRuntimeServiceResult:
    uid_value = str(os.getuid())
    last_error = "launchctl could not unload the AFKBOT agent automatically."
    for domain in (f"gui/{uid_value}", f"user/{uid_value}"):
        result = _run_command(["launchctl", "bootout", domain, str(path)], allow_failure=True)
        if result.returncode == 0:
            return ManagedRuntimeServiceResult(
                status="stopped",
                kind="launchd",
                path=path,
            )
        last_error = (
            f"launchctl bootout failed for domain {domain}: {_completed_process_error(result)}"
        )
    return ManagedRuntimeServiceResult(
        status="failed",
        reason=last_error,
        kind="launchd",
        path=path,
    )


def _resolve_current_afk_launcher_path() -> Path | None:
    argv0 = str(sys.argv[0] or "").strip()
    if argv0:
        discovered = shutil.which(argv0)
        if discovered:
            return Path(discovered).resolve(strict=False)
        candidate = Path(argv0).expanduser()
        if candidate.exists():
            return candidate.resolve(strict=False)
    managed_context = resolve_managed_install_context()
    if managed_context is not None:
        suffix = "afk.cmd" if os.name == "nt" else "afk"
        launcher = managed_context.bin_dir / suffix
        if launcher.exists():
            return launcher.resolve(strict=False)
    discovered_afk = shutil.which("afk")
    if discovered_afk:
        return Path(discovered_afk).resolve(strict=False)
    return None


def _render_systemd_user_unit(*, settings: Settings, launcher_path: Path) -> str:
    command = _render_service_start_guard()
    lines = [
        f"# {_HOST_SERVICE_MARKER}",
        "[Unit]",
        "Description=AFKBOT managed runtime",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={settings.root_dir}",
        f"Environment=AFKBOT_ROOT_DIR={_systemd_escape(str(settings.root_dir))}",
        f"Environment=AFKBOT_LAUNCHER={_systemd_escape(str(launcher_path))}",
        f"Environment=AFKBOT_SETUP_STATE_PATH={_systemd_escape(str(settings.setup_state_path))}",
        f"""ExecStart=/bin/sh -lc '{command}'""",
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def _render_systemd_system_unit(
    *,
    settings: Settings,
    launcher_path: Path,
    user_name: str,
    group_name: str,
    home_path: Path,
) -> str:
    command = _render_service_start_guard()
    lines = [
        f"# {_HOST_SERVICE_MARKER}",
        "[Unit]",
        "Description=AFKBOT managed runtime",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"User={user_name}",
        f"Group={group_name}",
        f"WorkingDirectory={settings.root_dir}",
        f"Environment=HOME={_systemd_escape(str(home_path))}",
        f"Environment=USER={_systemd_escape(user_name)}",
        f"Environment=LOGNAME={_systemd_escape(user_name)}",
        f"Environment=AFKBOT_ROOT_DIR={_systemd_escape(str(settings.root_dir))}",
        f"Environment=AFKBOT_LAUNCHER={_systemd_escape(str(launcher_path))}",
        f"Environment=AFKBOT_SETUP_STATE_PATH={_systemd_escape(str(settings.setup_state_path))}",
        f"""ExecStart=/bin/sh -lc '{command}'""",
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ]
    return "\n".join(lines)


def _render_launchd_plist(*, settings: Settings, launcher_path: Path) -> str:
    command = _render_service_start_guard()
    return "\n".join(
        [
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            f"<!-- {_HOST_SERVICE_MARKER} -->",
            "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
            "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">",
            "<plist version=\"1.0\">",
            "<dict>",
            "  <key>Label</key>",
            f"  <string>{_LAUNCHD_SERVICE_NAME}</string>",
            "  <key>ProgramArguments</key>",
            "  <array>",
            "    <string>/bin/sh</string>",
            "    <string>-lc</string>",
            f"    <string>{_xml_escape(command)}</string>",
            "  </array>",
            "  <key>WorkingDirectory</key>",
            f"  <string>{_xml_escape(str(settings.root_dir))}</string>",
            "  <key>EnvironmentVariables</key>",
            "  <dict>",
            "    <key>AFKBOT_ROOT_DIR</key>",
            f"    <string>{_xml_escape(str(settings.root_dir))}</string>",
            "    <key>AFKBOT_LAUNCHER</key>",
            f"    <string>{_xml_escape(str(launcher_path))}</string>",
            "    <key>AFKBOT_SETUP_STATE_PATH</key>",
            f"    <string>{_xml_escape(str(settings.setup_state_path))}</string>",
            "  </dict>",
            "  <key>RunAtLoad</key>",
            "  <true/>",
            "  <key>KeepAlive</key>",
            "  <dict>",
            "    <key>SuccessfulExit</key>",
            "    <false/>",
            "  </dict>",
            "</dict>",
            "</plist>",
            "",
        ]
    )


def _write_service_definition(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, mode)


def _write_systemd_system_definition(
    path: Path,
    content: str,
    *,
    mode: int,
) -> subprocess.CompletedProcess[str] | None:
    if os.geteuid() == 0:
        _write_service_definition(path, content, mode=mode)
        return None
    install_path = shutil.which("install")
    if install_path is None:
        return subprocess.CompletedProcess(
            ["/usr/bin/install"],
            1,
            stdout="",
            stderr="install command is unavailable",
        )
    temp_fd, temp_name = tempfile.mkstemp(prefix="afkbot-systemd-", suffix=".service")
    os.close(temp_fd)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(content, encoding="utf-8")
        os.chmod(temp_path, mode)
        result = _run_root_command(
            [
                install_path,
                "-D",
                "-m",
                format(mode, "o"),
                str(temp_path),
                str(path),
            ]
        )
        if result.returncode != 0:
            return result
        return None
    finally:
        temp_path.unlink(missing_ok=True)


def _ensure_systemd_user_enable_link(path: Path) -> None:
    wants_path = _systemd_user_wants_path(path)
    wants_path.parent.mkdir(parents=True, exist_ok=True)
    if wants_path.is_symlink():
        try:
            if wants_path.resolve(strict=False) == path.resolve(strict=False):
                return
        except OSError:
            pass
        wants_path.unlink(missing_ok=True)
    elif wants_path.exists():
        wants_path.unlink()
    wants_path.symlink_to(path)


def _systemd_user_wants_path(path: Path) -> Path:
    return path.parent / "default.target.wants" / path.name


def _managed_service_file_present(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return _HOST_SERVICE_MARKER in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _service_definition_is_unmanaged(path: Path) -> bool:
    return path.exists() and not _managed_service_file_present(path)


def _run_command(
    command: list[str],
    *,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if not allow_failure and result.returncode != 0:
        return result
    return result


def _run_root_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = _run_command(command, allow_failure=True)
    if result.returncode == 0 or os.geteuid() == 0:
        return result
    sudo_path = shutil.which("sudo")
    if sudo_path is None:
        return result
    return _run_command([sudo_path, "-n", *command], allow_failure=True)


def _describe_manager_state(status: ManagedRuntimeServiceStatus) -> str | None:
    if not status.installed or status.kind is None:
        return None
    if status.kind == "systemd-user":
        return _describe_systemd_manager_state(user=True, service_name=_SYSTEMD_SERVICE_NAME)
    if status.kind == "systemd-system":
        if status.path is None:
            return None
        return _describe_systemd_manager_state(
            user=False,
            service_name=_systemd_service_name_for_path(status.path),
        )
    if status.kind == "launchd":
        return _describe_launchd_manager_state()
    return None


def _describe_systemd_manager_state(*, user: bool, service_name: str) -> str:
    prefix = ["systemctl"]
    if user:
        prefix.append("--user")
    active = _run_command([*prefix, "is-active", service_name], allow_failure=True)
    enabled = _run_command([*prefix, "is-enabled", service_name], allow_failure=True)
    active_state = _read_command_state(active, fallback="unknown")
    enabled_state = _read_command_state(enabled, fallback="unknown")
    return f"active={active_state}, enabled={enabled_state}"


def _describe_launchd_manager_state() -> str:
    uid_value = str(os.getuid())
    for domain in (f"gui/{uid_value}", f"user/{uid_value}"):
        result = _run_command(
            ["launchctl", "print", f"{domain}/{_LAUNCHD_SERVICE_NAME}"],
            allow_failure=True,
        )
        if result.returncode != 0:
            continue
        for line in (result.stdout or "").splitlines():
            normalized = line.strip()
            if normalized.startswith("state = "):
                return normalized.removeprefix("state = ").strip()
        return "loaded"
    return "not_loaded"


def _read_command_state(result: subprocess.CompletedProcess[str], *, fallback: str) -> str:
    stdout = (result.stdout or "").strip()
    if stdout:
        return stdout.splitlines()[0]
    stderr = (result.stderr or "").strip()
    if stderr:
        return stderr.splitlines()[0]
    return fallback


def _finalize_start_result(
    result: ManagedRuntimeServiceResult,
    *,
    settings: Settings,
    should_be_running: bool,
    action: str,
) -> ManagedRuntimeServiceResult:
    if result.status not in {"installed", "restarted"}:
        return result
    if not should_be_running:
        return _annotate_started_service_result(
            result,
            inspection=inspect_managed_runtime_service(settings),
        )
    inspection = _wait_for_runtime_health(settings)
    if _inspection_matches_started_service(inspection):
        return _annotate_started_service_result(result, inspection=inspection)
    manager_state = inspection.manager_state or "unknown"
    diagnostics = _startup_failure_diagnostics(kind=result.kind, path=result.path)
    diagnostic_suffix = f" diagnostics={diagnostics}" if diagnostics else ""
    return ManagedRuntimeServiceResult(
        status="failed",
        reason=(
            f"AFKBOT service manager accepted the {action}, but the daemon did not become healthy. "
            f"manager_state={manager_state}, "
            f"runtime_health={'ok' if inspection.runtime_ok else 'down'}, "
            f"api_health={'ok' if inspection.api_ok else 'down'}.{diagnostic_suffix}"
        ),
        kind=result.kind,
        path=result.path,
    )


def _finalize_stop_result(
    result: ManagedRuntimeServiceResult,
    *,
    settings: Settings,
    should_be_stopped: bool,
) -> ManagedRuntimeServiceResult:
    if result.status != "stopped":
        return result
    if not should_be_stopped:
        return result
    inspection = _wait_for_runtime_stop(settings)
    if _inspection_matches_stopped_service(inspection):
        return result
    manager_state = inspection.manager_state or "unknown"
    return ManagedRuntimeServiceResult(
        status="failed",
        reason=(
            "AFKBOT service manager accepted the stop, but the daemon is still responding. "
            f"manager_state={manager_state}, "
            f"runtime_health={'ok' if inspection.runtime_ok else 'down'}, "
            f"api_health={'ok' if inspection.api_ok else 'down'}."
        ),
        kind=result.kind,
        path=result.path,
    )


def _wait_for_runtime_health(settings: Settings) -> ManagedRuntimeServiceInspection:
    deadline = time.monotonic() + _START_WAIT_TIMEOUT_SEC
    inspection = inspect_managed_runtime_service(settings)
    while time.monotonic() < deadline:
        if _inspection_matches_started_service(inspection):
            return inspection
        time.sleep(_POLL_INTERVAL_SEC)
        inspection = inspect_managed_runtime_service(settings)
    return inspection


def _wait_for_runtime_stop(settings: Settings) -> ManagedRuntimeServiceInspection:
    deadline = time.monotonic() + _STOP_WAIT_TIMEOUT_SEC
    inspection = inspect_managed_runtime_service(settings)
    while time.monotonic() < deadline:
        if _inspection_matches_stopped_service(inspection):
            return inspection
        time.sleep(_POLL_INTERVAL_SEC)
        inspection = inspect_managed_runtime_service(settings)
    return inspection


def _probe_runtime_stack(settings: Settings) -> RuntimeStackProbe:
    runtime_config = read_runtime_config(settings)
    host = str(runtime_config.get("runtime_host", settings.runtime_host)).strip() or settings.runtime_host
    runtime_port = resolve_default_runtime_port(
        settings=settings,
        host=host,
        runtime_config=runtime_config,
    )
    return probe_runtime_stack(host=host, runtime_port=runtime_port)


def _startup_failure_diagnostics(*, kind: str | None, path: Path | None) -> str | None:
    if kind == "systemd-user":
        return _systemd_failure_diagnostics(user=True, service_name=_SYSTEMD_SERVICE_NAME)
    if kind == "systemd-system" and path is not None:
        return _systemd_failure_diagnostics(
            user=False,
            service_name=_systemd_service_name_for_path(path),
        )
    if kind == "launchd":
        return _launchd_failure_diagnostics()
    return None


def _systemd_failure_diagnostics(*, user: bool, service_name: str) -> str | None:
    prefix = ["systemctl"]
    if user:
        prefix.append("--user")
    show_command = [
        *prefix,
        "show",
        service_name,
        "--property=ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,NRestarts",
    ]
    if user:
        show_result = _run_command(show_command, allow_failure=True)
        journal_result = _run_command(
            [
                "journalctl",
                "--user",
                "-u",
                service_name,
                "-n",
                "5",
                "--no-pager",
                "--output=cat",
            ],
            allow_failure=True,
        )
    else:
        show_result = _run_root_command(show_command)
        journal_result = _run_root_command(
            [
                "journalctl",
                "-u",
                service_name,
                "-n",
                "5",
                "--no-pager",
                "--output=cat",
            ]
        )
    show_summary = _summarize_systemd_show(show_result)
    journal_summary = _summarize_recent_log_output(journal_result)
    parts = [part for part in (show_summary, journal_summary) if part]
    if not parts:
        return None
    return " ".join(parts)


def _launchd_failure_diagnostics() -> str | None:
    uid_value = str(os.getuid())
    for domain in (f"gui/{uid_value}", f"user/{uid_value}"):
        result = _run_command(
            ["launchctl", "print", f"{domain}/{_LAUNCHD_SERVICE_NAME}"],
            allow_failure=True,
        )
        summary = _summarize_recent_log_output(result)
        if summary:
            return summary
    return None


def _summarize_systemd_show(result: subprocess.CompletedProcess[str]) -> str | None:
    if result.returncode != 0:
        return None
    fields: list[str] = []
    for raw_line in (result.stdout or "").splitlines():
        line = raw_line.strip()
        if not line or line.endswith("="):
            continue
        fields.append(line)
    if not fields:
        return None
    return "systemd_show=" + ";".join(fields)


def _summarize_recent_log_output(result: subprocess.CompletedProcess[str]) -> str | None:
    output = (result.stdout or result.stderr or "").strip()
    if not output:
        return None
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    excerpt = " | ".join(lines[-3:])
    if len(excerpt) > 400:
        excerpt = excerpt[-400:]
    return f"recent_log={excerpt}"


def _inspection_matches_started_service(inspection: ManagedRuntimeServiceInspection) -> bool:
    if not inspection.running:
        return False
    manager_state = str(inspection.manager_state or "").strip().lower()
    if inspection.kind in {"systemd-user", "systemd-system"}:
        active_state = manager_state.split(",", maxsplit=1)[0].strip()
        return active_state == "active=active"
    if inspection.kind == "launchd":
        return manager_state in {"running", "loaded", "waiting"}
    return inspection.installed


def _inspection_matches_stopped_service(inspection: ManagedRuntimeServiceInspection) -> bool:
    return not inspection.runtime_ok and not inspection.api_ok


def _annotate_started_service_result(
    result: ManagedRuntimeServiceResult,
    *,
    inspection: ManagedRuntimeServiceInspection,
) -> ManagedRuntimeServiceResult:
    manager_state = str(inspection.manager_state or "").strip().lower()
    reason = result.reason
    if inspection.kind == "systemd-user" and "enabled=enabled" in manager_state:
        reason = _append_reason(
            reason,
            "Autostart after reboot requires an active user session or "
            f"`sudo loginctl enable-linger {_current_linux_user_name()}`.",
        )
    if inspection.kind == "systemd-system" and "enabled=disabled" in manager_state:
        reason = _append_reason(
            reason,
            "Boot autostart remains disabled. "
            f"Run `sudo systemctl enable {_systemd_service_name_for_path(result.path)}` if needed."
            if result.path is not None
            else "Boot autostart remains disabled.",
        )
    if inspection.kind == "launchd":
        reason = _append_reason(
            reason,
            "launchd LaunchAgents start after user login for this macOS user.",
        )
    if reason == result.reason:
        return result
    return ManagedRuntimeServiceResult(
        status=result.status,
        reason=reason,
        kind=result.kind,
        path=result.path,
    )


def _systemd_user_failure_reason(result: subprocess.CompletedProcess[str]) -> str:
    base = _completed_process_error(result)
    lowered = base.lower()
    user_name = _current_linux_user_name()
    if "failed to connect to bus" in lowered or "no medium found" in lowered:
        return (
            f"{base}. The AFKBOT user service was installed and enabled on disk, "
            "but the current systemd user session is unavailable. "
            f"Start a login session for this user or run `sudo loginctl enable-linger {user_name}` "
            "to restore reboot autostart without a live login."
        )
    return f"{base}. Restart `afk start` manually if systemd user services are unavailable."


def _systemd_system_failure_reason(
    result: subprocess.CompletedProcess[str],
    *,
    action: str,
) -> str:
    base = _completed_process_error(result)
    user_name = _current_linux_user_name()
    return (
        f"{base}. System-level AFKBOT service {action} requires root or passwordless sudo. "
        f"If you cannot grant that, keep the user-level service and run "
        f"`sudo loginctl enable-linger {user_name}` for reboot autostart."
    )


def _linux_system_service_fallback_reason() -> str:
    user_name = _current_linux_user_name()
    return (
        "Using a user-level systemd service because a system-level install was unavailable in this shell. "
        f"For reboot autostart without an active login, run `sudo loginctl enable-linger {user_name}`."
    )


def _combine_linux_install_failures(
    *,
    system_result: ManagedRuntimeServiceResult,
    user_result: ManagedRuntimeServiceResult,
) -> ManagedRuntimeServiceResult:
    return ManagedRuntimeServiceResult(
        status="manual_restart_required",
        reason=(
            "AFKBOT could not provision a managed Linux service automatically. "
            f"system_service={system_result.reason or system_result.status}; "
            f"user_service={user_result.reason or user_result.status}"
        ),
        kind=user_result.kind or system_result.kind,
        path=user_result.path or system_result.path,
    )


def _remove_systemd_user_service(
    path: Path,
    *,
    allow_failure: bool = False,
) -> ManagedRuntimeServiceResult:
    _run_command(
        ["systemctl", "--user", "disable", "--now", _SYSTEMD_SERVICE_NAME],
        allow_failure=True,
    )
    _systemd_user_wants_path(path).unlink(missing_ok=True)
    path.unlink(missing_ok=True)
    _run_command(["systemctl", "--user", "daemon-reload"], allow_failure=True)
    return ManagedRuntimeServiceResult(status="removed", kind="systemd-user", path=path)


def _remove_systemd_system_service(
    path: Path,
    *,
    allow_failure: bool = False,
) -> ManagedRuntimeServiceResult:
    service_name = _systemd_service_name_for_path(path)
    commands = [
        (["systemctl", "disable", "--now", service_name], True),
        (["rm", "-f", str(path)], allow_failure),
        (["systemctl", "daemon-reload"], allow_failure),
    ]
    for command, optional in commands:
        result = _run_root_command(command)
        if result.returncode == 0:
            continue
        if optional:
            continue
        return ManagedRuntimeServiceResult(
            status="manual_restart_required",
            reason=_systemd_system_failure_reason(result, action="remove"),
            kind="systemd-system",
            path=path,
        )
    return ManagedRuntimeServiceResult(status="removed", kind="systemd-system", path=path)


def _resolve_linux_service_account() -> _LinuxServiceAccount | None:
    sudo_user = str(os.getenv("SUDO_USER") or "").strip()
    if os.geteuid() == 0 and sudo_user:
        try:
            user_record = pwd.getpwnam(sudo_user)
            group_id = int(str(os.getenv("SUDO_GID") or "").strip()) if os.getenv("SUDO_GID") else user_record.pw_gid
            group_record = grp.getgrgid(group_id)
        except (KeyError, ValueError):
            return None
        return _LinuxServiceAccount(
            user_name=user_record.pw_name,
            group_name=group_record.gr_name,
            home_path=Path(user_record.pw_dir),
        )
    try:
        user_record = pwd.getpwuid(os.getuid())
        group_record = grp.getgrgid(os.getgid())
    except KeyError:
        return None
    return _LinuxServiceAccount(
        user_name=user_record.pw_name,
        group_name=group_record.gr_name,
        home_path=Path(user_record.pw_dir),
    )


def _current_linux_user_name() -> str:
    account = _resolve_linux_service_account()
    if account is not None and account.user_name.strip():
        return account.user_name
    env_user = str(os.getenv("USER") or os.getenv("LOGNAME") or "").strip()
    if env_user:
        return env_user
    return "<user>"


def _render_service_start_guard() -> str:
    return 'exec "$AFKBOT_LAUNCHER" service run-managed'


def _preferred_systemd_system_service_path() -> Path:
    return Path("/etc/systemd/system") / _systemd_service_name_for_current_user()


def _systemd_service_name_for_current_user() -> str:
    user_name = _sanitize_systemd_unit_component(_current_linux_user_name())
    return f"{_SYSTEMD_SYSTEM_SERVICE_BASENAME}-{user_name}.service"


def _systemd_service_name_for_path(path: Path) -> str:
    return path.name


def _sanitize_systemd_unit_component(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return normalized.strip("-_") or "user"


def _service_file_belongs_to_current_user(path: Path) -> bool:
    account = _resolve_linux_service_account()
    if account is None:
        return False
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        if not line.startswith("User="):
            continue
        return line.partition("=")[2].strip() == account.user_name
    return False


def _legacy_systemd_system_service_belongs_to_current_user() -> bool:
    return _managed_service_file_present(_SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH) and _service_file_belongs_to_current_user(
        _SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH
    )


def _cleanup_linux_secondary_services(active_system_path: Path | None) -> str | None:
    if active_system_path is None:
        return None
    notices: list[str] = []
    if _managed_service_file_present(_SYSTEMD_USER_SERVICE_PATH):
        result = _remove_systemd_user_service(_SYSTEMD_USER_SERVICE_PATH, allow_failure=True)
        if result.status != "removed":
            notices.append("previous user-level systemd unit was left in place")
    if (
        active_system_path != _SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH
        and _legacy_systemd_system_service_belongs_to_current_user()
    ):
        result = _remove_systemd_system_service(_SYSTEMD_LEGACY_SYSTEM_SERVICE_PATH, allow_failure=True)
        if result.status != "removed":
            notices.append("legacy system-level AFKBOT unit was left in place")
    if not notices:
        return None
    return " ".join(notices)


def _append_cleanup_notice(
    result: ManagedRuntimeServiceResult,
    notice: str | None,
) -> ManagedRuntimeServiceResult:
    if not notice:
        return result
    return ManagedRuntimeServiceResult(
        status=result.status,
        reason=_append_reason(result.reason, notice),
        kind=result.kind,
        path=result.path,
    )


def _append_reason(current: str | None, extra: str) -> str:
    normalized = (current or "").strip()
    if not normalized:
        return extra
    if extra in normalized:
        return normalized
    return f"{normalized} {extra}"


def _completed_process_error(result: subprocess.CompletedProcess[str]) -> str:
    stderr = (result.stderr or "").strip()
    if stderr:
        return stderr.splitlines()[0]
    stdout = (result.stdout or "").strip()
    if stdout:
        return stdout.splitlines()[0]
    return f"exit code {result.returncode}"


def _systemd_escape(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
