"""Managed Lightpanda runtime helpers for install/start/stop/status flows."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import signal
import socket
import subprocess
import time
from urllib.request import urlopen

from afkbot.browser_backends import LIGHTPANDA_DEFAULT_CDP_URL
from afkbot.services.browser_cdp import browser_cdp_host_port, browser_cdp_url_is_local
from afkbot.settings import Settings

_LINUX_X86_64_URL = (
    "https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-x86_64-linux"
)
_MACOS_ARM64_URL = (
    "https://github.com/lightpanda-io/browser/releases/download/nightly/lightpanda-aarch64-macos"
)
_DOWNLOAD_TIMEOUT_SEC = 60.0
_START_TIMEOUT_SEC = 10.0
_STOP_TIMEOUT_SEC = 5.0


@dataclass(frozen=True, slots=True)
class LightpandaInstallResult:
    """Outcome of installing the managed Lightpanda binary."""

    ok: bool
    error_code: str | None
    reason: str
    changed: bool
    binary_path: str


@dataclass(frozen=True, slots=True)
class LightpandaRunResult:
    """Outcome of starting or stopping the managed Lightpanda runtime."""

    ok: bool
    error_code: str | None
    reason: str
    changed: bool
    running: bool
    pid: int | None
    binary_path: str
    log_path: str


@dataclass(frozen=True, slots=True)
class LightpandaManagedStatus:
    """Current status of the managed Lightpanda binary/runtime files."""

    supported: bool
    endpoint_url: str
    endpoint_is_local: bool
    binary_path: str
    binary_installed: bool
    pid: int | None
    running: bool
    log_path: str


def get_lightpanda_managed_status(settings: Settings) -> LightpandaManagedStatus:
    """Return current managed Lightpanda binary/runtime state."""

    binary_path = resolve_lightpanda_binary_path(settings)
    endpoint_url = (settings.browser_cdp_url or LIGHTPANDA_DEFAULT_CDP_URL).strip()
    pid_path = _pid_file(settings)
    pid = _read_pid_file(pid_path)
    if pid is not None and not _pid_matches_expected_binary(pid, binary_path=binary_path):
        pid = None
        pid_path.unlink(missing_ok=True)
    return LightpandaManagedStatus(
        supported=_platform_binary_url() is not None,
        endpoint_url=endpoint_url,
        endpoint_is_local=browser_cdp_url_is_local(endpoint_url),
        binary_path=str(binary_path),
        binary_installed=binary_path.exists(),
        pid=pid,
        running=pid is not None,
        log_path=str(_log_file(settings)),
    )


def install_lightpanda_binary(
    *, settings: Settings, force: bool = False
) -> LightpandaInstallResult:
    """Install the managed Lightpanda nightly binary when the platform is supported."""

    binary_url = _platform_binary_url()
    binary_path = resolve_lightpanda_binary_path(settings)
    if binary_url is None:
        return LightpandaInstallResult(
            ok=False,
            error_code="lightpanda_binary_unsupported_platform",
            reason=(
                "Managed Lightpanda binary install is only supported on Linux x86_64 and macOS arm64. "
                "Use an external Lightpanda process and keep the CDP URL configured manually."
            ),
            changed=False,
            binary_path=str(binary_path),
        )
    if binary_path.exists() and not force:
        return LightpandaInstallResult(
            ok=True,
            error_code=None,
            reason="Managed Lightpanda binary is already installed.",
            changed=False,
            binary_path=str(binary_path),
        )

    binary_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = binary_path.with_suffix(".tmp")
    try:
        with (
            urlopen(binary_url, timeout=_DOWNLOAD_TIMEOUT_SEC) as response,
            tmp_path.open("wb") as target,
        ):  # noqa: S310
            shutil.copyfileobj(response, target)
        tmp_path.chmod(0o755)
        tmp_path.replace(binary_path)
    except OSError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return LightpandaInstallResult(
            ok=False,
            error_code="lightpanda_binary_install_failed",
            reason=f"Failed to install Lightpanda binary: {exc}",
            changed=False,
            binary_path=str(binary_path),
        )

    return LightpandaInstallResult(
        ok=True,
        error_code=None,
        reason=f"Managed Lightpanda binary installed at {binary_path}.",
        changed=True,
        binary_path=str(binary_path),
    )


def start_lightpanda_runtime(
    *,
    settings: Settings,
    timeout_sec: float = _START_TIMEOUT_SEC,
) -> LightpandaRunResult:
    """Start the managed Lightpanda CDP server and wait until the socket is ready."""

    configured_endpoint = (settings.browser_cdp_url or "").strip()
    if not configured_endpoint:
        binary_path = resolve_lightpanda_binary_path(settings)
        return LightpandaRunResult(
            ok=False,
            error_code="browser_cdp_url_missing",
            reason=(
                "Browser CDP URL is not configured for managed Lightpanda. "
                f"Set `afk browser cdp-url {LIGHTPANDA_DEFAULT_CDP_URL}` or rerun `afk browser install`."
            ),
            changed=False,
            running=False,
            pid=None,
            binary_path=str(binary_path),
            log_path=str(_log_file(settings)),
        )
    status = get_lightpanda_managed_status(settings)
    if status.running:
        return LightpandaRunResult(
            ok=True,
            error_code=None,
            reason="Managed Lightpanda runtime is already running.",
            changed=False,
            running=True,
            pid=status.pid,
            binary_path=status.binary_path,
            log_path=status.log_path,
        )
    if not status.endpoint_is_local:
        return LightpandaRunResult(
            ok=False,
            error_code="lightpanda_cdp_url_not_local",
            reason=(
                f"Managed Lightpanda can only bind to a local CDP URL, got {status.endpoint_url}. "
                "Use a local endpoint such as http://127.0.0.1:9222 or run the external browser yourself."
            ),
            changed=False,
            running=False,
            pid=None,
            binary_path=status.binary_path,
            log_path=status.log_path,
        )
    if not status.binary_installed:
        return LightpandaRunResult(
            ok=False,
            error_code="lightpanda_binary_missing",
            reason="Managed Lightpanda binary is not installed. Run `afk browser install` first.",
            changed=False,
            running=False,
            pid=None,
            binary_path=status.binary_path,
            log_path=status.log_path,
        )

    host, port = browser_cdp_host_port(status.endpoint_url)
    if _socket_is_ready(host=host, port=port):
        return LightpandaRunResult(
            ok=False,
            error_code="lightpanda_port_in_use",
            reason=(
                f"A browser is already listening on {host}:{port}. "
                "Use the existing endpoint or stop it before starting managed Lightpanda."
            ),
            changed=False,
            running=False,
            pid=None,
            binary_path=status.binary_path,
            log_path=status.log_path,
        )

    log_path = _log_file(settings)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_file:
        env = dict(os.environ)
        if settings.browser_lightpanda_disable_telemetry:
            env["LIGHTPANDA_DISABLE_TELEMETRY"] = "true"
        process = subprocess.Popen(  # noqa: S603
            [status.binary_path, "serve", "--host", host, "--port", str(port)],
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    _pid_file(settings).write_text(f"{process.pid}\n", encoding="utf-8")
    if _wait_for_runtime_ready(process=process, host=host, port=port, timeout_sec=timeout_sec):
        return LightpandaRunResult(
            ok=True,
            error_code=None,
            reason=f"Managed Lightpanda runtime started on {host}:{port}.",
            changed=True,
            running=True,
            pid=process.pid,
            binary_path=status.binary_path,
            log_path=str(log_path),
        )

    _terminate_pid(process.pid, timeout_sec=_STOP_TIMEOUT_SEC)
    _pid_file(settings).unlink(missing_ok=True)
    return LightpandaRunResult(
        ok=False,
        error_code="lightpanda_start_failed",
        reason=(
            f"Managed Lightpanda runtime did not become ready on {host}:{port}. "
            f"Inspect logs at {log_path}."
        ),
        changed=False,
        running=False,
        pid=None,
        binary_path=status.binary_path,
        log_path=str(log_path),
    )


def stop_lightpanda_runtime(
    *, settings: Settings, timeout_sec: float = _STOP_TIMEOUT_SEC
) -> LightpandaRunResult:
    """Stop the managed Lightpanda process tracked by the local pid file."""

    status = get_lightpanda_managed_status(settings)
    pid_path = _pid_file(settings)
    if status.pid is None:
        pid_path.unlink(missing_ok=True)
        return LightpandaRunResult(
            ok=True,
            error_code=None,
            reason="Managed Lightpanda runtime is already stopped.",
            changed=False,
            running=False,
            pid=None,
            binary_path=status.binary_path,
            log_path=status.log_path,
        )
    stopped = _terminate_pid(status.pid, timeout_sec=timeout_sec)
    if stopped:
        pid_path.unlink(missing_ok=True)
    return LightpandaRunResult(
        ok=stopped,
        error_code=None if stopped else "lightpanda_stop_failed",
        reason=(
            "Managed Lightpanda runtime stopped."
            if stopped
            else f"Failed to stop managed Lightpanda runtime pid={status.pid}."
        ),
        changed=stopped,
        running=not stopped,
        pid=None if stopped else status.pid,
        binary_path=status.binary_path,
        log_path=status.log_path,
    )


def resolve_lightpanda_binary_path(settings: Settings) -> Path:
    """Return the configured or default managed Lightpanda binary path."""

    configured = (settings.browser_lightpanda_binary_path or "").strip()
    if not configured:
        return settings.root_dir.resolve() / "bin" / "lightpanda"
    path = Path(configured).expanduser()
    if path.is_absolute():
        return path
    return settings.root_dir.resolve() / path


def lightpanda_runtime_hint(settings: Settings) -> str:
    """Return one actionable remediation string for the current Lightpanda setup."""

    if not (settings.browser_cdp_url or "").strip():
        return (
            f"Set `afk browser cdp-url {LIGHTPANDA_DEFAULT_CDP_URL}` or rerun "
            "`afk browser install` to configure managed Lightpanda."
        )
    status = get_lightpanda_managed_status(settings)
    if not status.endpoint_is_local:
        return (
            "Configured CDP endpoint is external. Start that browser separately or "
            "set `afk browser cdp-url http://127.0.0.1:9222` to use managed Lightpanda."
        )
    if status.binary_installed:
        return "Run `afk browser start` to launch the managed Lightpanda CDP server."
    if status.supported:
        return "Run `afk browser install` to install the managed Lightpanda binary."
    return (
        "Managed Lightpanda binary is not supported on this platform. "
        "Run Lightpanda separately via a local standalone installation."
    )


def _state_dir(settings: Settings) -> Path:
    return settings.root_dir.resolve() / "tmp" / "lightpanda"


def _pid_file(settings: Settings) -> Path:
    return _state_dir(settings) / "lightpanda.pid"


def _log_file(settings: Settings) -> Path:
    return _state_dir(settings) / "lightpanda.log"


def _platform_binary_url() -> str | None:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "linux" and machine in {"x86_64", "amd64"}:
        return _LINUX_X86_64_URL
    if system == "darwin" and machine in {"arm64", "aarch64"}:
        return _MACOS_ARM64_URL
    return None


def _read_pid_file(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_matches_expected_binary(pid: int, *, binary_path: Path) -> bool:
    if not _pid_is_alive(pid):
        return False
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    if completed.returncode != 0:
        return False
    command = (completed.stdout or "").strip()
    if not command:
        return False
    executable = command.split()[0]
    expected = str(binary_path.resolve(strict=False))
    if executable == expected:
        return True
    return Path(executable).resolve(strict=False) == Path(expected)


def _terminate_pid(pid: int, *, timeout_sec: float) -> bool:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return not _pid_is_alive(pid)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_is_alive(pid)


def _socket_is_ready(*, host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _wait_for_runtime_ready(
    *,
    process: subprocess.Popen[bytes],
    host: str,
    port: int,
    timeout_sec: float,
) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if _socket_is_ready(host=host, port=port):
            return True
        time.sleep(0.1)
    return False
