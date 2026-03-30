"""Update helpers for source checkouts, legacy managed installs, and uv tool installs."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

from afkbot.services.managed_install import (
    ManagedInstallContext,
    build_next_app_dir,
    cleanup_staged_source,
    prune_stale_app_dirs,
    resolve_managed_install_context,
    stage_source_snapshot,
    write_managed_launcher,
)
from afkbot.services.setup.runtime_store import read_runtime_config
from afkbot.settings import Settings

_HOST_SERVICE_MARKER = "afkbot-managed-runtime-service"
_SYSTEMD_SERVICE_PATH = Path("/etc/systemd/system/afkbot.service")
_LAUNCHD_SERVICE_NAME = "io.afkbot.afkbot"
_DEFAULT_API_PORT_OFFSET = 1
_CODE_CHECKOUT_ROOT = Path(__file__).resolve(strict=False).parents[2]
_UV_TOOL_PACKAGE = "afkbotio"


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Summary of one local-runtime update run."""

    install_mode: str
    source_updated: bool
    runtime_restarted: bool
    maintenance_applied: bool
    details: tuple[str, ...]


class UpdateRuntimeError(RuntimeError):
    """Raised when managed runtime update cannot complete."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def run_update(settings: Settings) -> UpdateResult:
    """Update the local AFKBOT source checkout and apply pending maintenance."""

    managed_context = resolve_managed_install_context()
    if managed_context is not None:
        return _run_managed_update(settings=settings, context=managed_context)
    if _is_source_checkout_install():
        return _run_host_update(settings=settings)
    return _run_uv_tool_update(settings=settings)


def format_update_success(result: UpdateResult) -> str:
    """Render short human-readable update summary."""

    lines = ["AFKBOT update complete."]
    lines.append(f"Install mode: {result.install_mode}")
    lines.append("Source: updated" if result.source_updated else "Source: already up to date")
    lines.append("Maintenance: applied" if result.maintenance_applied else "Maintenance: skipped")
    lines.append("Runtime: restarted" if result.runtime_restarted else "Runtime: no managed restart")
    for detail in result.details:
        lines.append(detail)
    return "\n".join(lines)


def _run_host_update(*, settings: Settings) -> UpdateResult:
    project_root = _resolve_host_checkout_root(settings)
    _assert_host_git_checkout(project_root)
    branch = _git_stdout(project_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch:
        raise UpdateRuntimeError(
            error_code="update_prereq_failed",
            reason="update requires a branch-backed git checkout",
        )
    if _git_worktree_is_dirty(project_root):
        raise UpdateRuntimeError(
            error_code="update_prereq_failed",
            reason="update requires a clean git worktree; commit or stash local changes first",
        )
    if not _git_has_origin(project_root):
        raise UpdateRuntimeError(
            error_code="update_prereq_failed",
            reason="update requires an origin remote on the current checkout",
        )

    before_head = _git_stdout(project_root, "rev-parse", "HEAD")
    history_rewritten = _sync_host_checkout(project_root=project_root, branch=branch)
    after_head = _git_stdout(project_root, "rev-parse", "HEAD")
    source_updated = bool(before_head and after_head and before_head != after_head)

    if source_updated:
        _run_checked(
            [sys.executable, "-m", "pip", "install", "-e", str(project_root)],
            cwd=project_root,
            error_code="update_failed",
            fallback="failed to refresh local Python environment",
        )

    _run_afk_subcommand(settings=settings, args=("doctor", "--no-integrations", "--no-upgrades"))
    _run_afk_subcommand(settings=settings, args=("upgrade", "apply", "--quiet"))

    runtime_restarted = _restart_managed_host_runtime_service()
    details = [
        f"Git branch: {branch}",
        (
            f"Git source reset to origin/{branch} after history rewrite"
            if history_rewritten
            else ""
        ),
        (
            "Runtime health: ok"
            if (runtime_restarted and _wait_for_local_health(settings=settings))
            else ""
        ),
        "Managed host service not found; restart manually with `afk start`"
        if not runtime_restarted
        else "",
    ]
    return UpdateResult(
        install_mode="host",
        source_updated=source_updated,
        runtime_restarted=runtime_restarted,
        maintenance_applied=True,
        details=tuple(detail for detail in details if detail),
    )


def _run_managed_update(*, settings: Settings, context: ManagedInstallContext) -> UpdateResult:
    """Update one managed install by staging a fresh source snapshot beside the current app tree."""

    staged_source: Path | None = None
    try:
        staged_source = stage_source_snapshot(context)
        next_app_dir = build_next_app_dir(context)
        try:
            next_app_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_source), str(next_app_dir))
        finally:
            if staged_source is not None:
                cleanup_staged_source(staged_source)
    except (OSError, ValueError) as exc:
        raise UpdateRuntimeError(
            error_code="update_failed",
            reason=f"failed to stage managed source snapshot: {exc}",
        ) from exc

    _run_checked(
        [sys.executable, "-m", "pip", "install", "-e", str(next_app_dir)],
        cwd=next_app_dir,
        error_code="update_failed",
        fallback="failed to refresh managed Python environment",
    )
    try:
        write_managed_launcher(
            context=context,
            python_executable=Path(sys.executable),
            app_dir=next_app_dir,
        )
        prune_stale_app_dirs(context=context, keep_paths=(context.app_dir, next_app_dir))
    except OSError as exc:
        raise UpdateRuntimeError(
            error_code="update_failed",
            reason=f"failed to refresh managed launcher: {exc}",
        ) from exc

    _run_afk_subcommand(settings=settings, args=("doctor", "--no-integrations", "--no-upgrades"))
    _run_afk_subcommand(settings=settings, args=("upgrade", "apply", "--quiet"))

    runtime_restarted = _restart_managed_host_runtime_service()
    details = [
        f"Managed source ref: {context.source_ref}",
        f"Managed source dir: {next_app_dir}",
        (
            "Runtime health: ok"
            if (runtime_restarted and _wait_for_local_health(settings=settings))
            else ""
        ),
        "Managed host service not found; restart manually with `afk start`"
        if not runtime_restarted
        else "",
    ]
    return UpdateResult(
        install_mode="managed",
        source_updated=True,
        runtime_restarted=runtime_restarted,
        maintenance_applied=True,
        details=tuple(detail for detail in details if detail),
    )


def _run_uv_tool_update(*, settings: Settings) -> UpdateResult:
    """Update one uv-installed AFKBOT tool environment and apply maintenance in a new process."""

    uv_executable = _resolve_uv_executable()
    _run_checked(
        [str(uv_executable), "tool", "upgrade", _UV_TOOL_PACKAGE, "--reinstall"],
        error_code="update_failed",
        fallback="failed to upgrade AFKBOT via uv tool",
    )
    afk_executable = _resolve_uv_tool_afk_executable(uv_executable=uv_executable)
    _run_afk_executable(
        executable=afk_executable,
        settings=settings,
        args=("upgrade", "apply", "--quiet"),
    )
    _run_afk_executable(
        executable=afk_executable,
        settings=settings,
        args=("doctor", "--no-integrations", "--no-upgrades"),
    )

    runtime_restarted = _restart_managed_host_runtime_service()
    details = [
        f"Tool package: {_UV_TOOL_PACKAGE}",
        f"Tool executable: {afk_executable}",
        (
            "Runtime health: ok"
            if (runtime_restarted and _wait_for_local_health(settings=settings))
            else ""
        ),
        "Managed host service not found; restart manually with `afk start`"
        if not runtime_restarted
        else "",
    ]
    return UpdateResult(
        install_mode="uv-tool",
        source_updated=True,
        runtime_restarted=runtime_restarted,
        maintenance_applied=True,
        details=tuple(detail for detail in details if detail),
    )


def _resolve_host_checkout_root(settings: Settings) -> Path:
    """Return the git checkout root for source-based updates."""

    candidates = (
        settings.root_dir.resolve(strict=False),
        _CODE_CHECKOUT_ROOT.resolve(strict=False),
    )
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / ".git").exists() and (candidate / "pyproject.toml").exists():
            return candidate
    return settings.root_dir.resolve(strict=False)


def _is_source_checkout_install() -> bool:
    """Return whether the active AFKBOT command is running from the source checkout."""

    root = _CODE_CHECKOUT_ROOT.resolve(strict=False)
    return (root / ".git").exists() and (root / "pyproject.toml").exists()


def _assert_host_git_checkout(project_root: Path) -> None:
    if (project_root / ".git").exists():
        return
    raise UpdateRuntimeError(
        error_code="update_prereq_failed",
        reason=f"update requires a git checkout: {project_root}",
    )


def _sync_host_checkout(*, project_root: Path, branch: str) -> bool:
    _run_checked(
        ["git", "-C", str(project_root), "fetch", "--depth", "1", "--no-tags", "origin", branch],
        error_code="update_failed",
        fallback=f"failed to fetch latest source for branch {branch}",
    )
    fetched_head = _git_stdout(project_root, "rev-parse", "FETCH_HEAD")
    current_head = _git_stdout(project_root, "rev-parse", "HEAD")
    if fetched_head and current_head and fetched_head == current_head:
        return False

    if _git_is_ancestor(project_root, ancestor="HEAD", descendant="FETCH_HEAD"):
        _run_checked(
            ["git", "-C", str(project_root), "merge", "--ff-only", "FETCH_HEAD"],
            error_code="update_failed",
            fallback="failed to fast-forward checkout",
        )
        return False

    _run_checked(
        ["git", "-C", str(project_root), "reset", "--hard", "FETCH_HEAD"],
        error_code="update_failed",
        fallback=f"failed to reset checkout to origin/{branch}",
    )
    return True


def _git_has_origin(project_root: Path) -> bool:
    result = _run_command(
        ["git", "-C", str(project_root), "remote", "get-url", "origin"],
        cwd=project_root,
    )
    return result.returncode == 0


def _resolve_uv_executable() -> Path:
    """Return the uv executable used to manage tool installs."""

    discovered = shutil.which("uv")
    if discovered:
        return Path(discovered).resolve(strict=False)
    user_bin_dir = _default_user_bin_dir()
    suffix = "uv.exe" if os.name == "nt" else "uv"
    candidate = user_bin_dir / suffix
    if candidate.exists():
        return candidate.resolve(strict=False)
    raise UpdateRuntimeError(
        error_code="update_prereq_failed",
        reason="uv is required to update this AFKBOT install; reinstall with the hosted installer or install uv first",
    )


def _default_user_bin_dir() -> Path:
    """Return the expected user-local executable directory used by uv."""

    xdg_bin_home = os.getenv("XDG_BIN_HOME")
    if xdg_bin_home:
        return Path(xdg_bin_home).expanduser()
    xdg_data_home = os.getenv("XDG_DATA_HOME")
    if xdg_data_home:
        return (Path(xdg_data_home).expanduser() / ".." / "bin").resolve(strict=False)
    return (Path.home() / ".local" / "bin").resolve(strict=False)


def _resolve_uv_tool_bin_dir(*, uv_executable: Path) -> Path:
    """Return the uv tool executable directory."""

    output = _run_checked(
        [str(uv_executable), "tool", "dir", "--bin"],
        error_code="update_failed",
        fallback="failed to locate uv tool executable directory",
    ).stdout.strip()
    if not output:
        raise UpdateRuntimeError(
            error_code="update_failed",
            reason="uv did not report a tool executable directory",
        )
    return Path(output).resolve(strict=False)


def _resolve_uv_tool_afk_executable(*, uv_executable: Path) -> Path:
    """Return the installed AFKBOT executable inside the uv tool bin directory."""

    bin_dir = _resolve_uv_tool_bin_dir(uv_executable=uv_executable)
    candidate = bin_dir / ("afk.cmd" if os.name == "nt" else "afk")
    if candidate.exists():
        return candidate
    raise UpdateRuntimeError(
        error_code="update_failed",
        reason=f"AFKBOT executable not found in uv tool bin directory: {candidate}",
    )


def _git_stdout(project_root: Path, *args: str) -> str:
    result = _run_command(["git", "-C", str(project_root), *args], cwd=project_root)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _git_worktree_is_dirty(project_root: Path) -> bool:
    unstaged = _run_command(
        ["git", "-C", str(project_root), "diff", "--quiet", "--ignore-submodules", "--"],
        cwd=project_root,
    )
    if unstaged.returncode != 0:
        return True
    staged = _run_command(
        ["git", "-C", str(project_root), "diff", "--cached", "--quiet", "--ignore-submodules", "--"],
        cwd=project_root,
    )
    if staged.returncode != 0:
        return True
    untracked = _run_command(
        ["git", "-C", str(project_root), "ls-files", "--others", "--exclude-standard"],
        cwd=project_root,
    )
    return bool(untracked.stdout.strip())


def _git_is_ancestor(project_root: Path, *, ancestor: str, descendant: str) -> bool:
    result = _run_command(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=project_root,
    )
    return result.returncode == 0


def _run_afk_subcommand(*, settings: Settings, args: tuple[str, ...]) -> None:
    _run_checked(
        [sys.executable, "-m", "afkbot.cli.main", *args],
        cwd=settings.root_dir,
        error_code="update_failed",
        fallback=f"failed to run AFKBOT command: {' '.join(args)}",
    )


def _run_afk_executable(*, executable: Path, settings: Settings, args: tuple[str, ...]) -> None:
    _run_checked(
        [str(executable), *args],
        cwd=settings.root_dir,
        error_code="update_failed",
        fallback=f"failed to run AFKBOT command: {' '.join(args)}",
    )


def _restart_managed_host_runtime_service() -> bool:
    system_name = platform.system().lower()
    if system_name == "linux":
        if not _SYSTEMD_SERVICE_PATH.exists():
            return False
        if _HOST_SERVICE_MARKER not in _safe_read_text(_SYSTEMD_SERVICE_PATH):
            return False
        _run_checked(
            ["systemctl", "daemon-reload"],
            error_code="update_failed",
            fallback="failed to reload systemd units",
        )
        _run_checked(
            ["systemctl", "restart", "afkbot.service"],
            error_code="update_failed",
            fallback="failed to restart managed host runtime service",
        )
        return True
    if system_name == "darwin":
        launchd_path = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_SERVICE_NAME}.plist"
        if not launchd_path.exists():
            return False
        if _HOST_SERVICE_MARKER not in _safe_read_text(launchd_path):
            return False
        uid = _run_checked(
            ["id", "-u"],
            error_code="update_prereq_failed",
            fallback="failed to resolve uid",
        ).stdout.strip()
        for domain in (f"gui/{uid}", f"user/{uid}"):
            _run_command(["launchctl", "bootout", domain, str(launchd_path)])
            result = _run_command(["launchctl", "bootstrap", domain, str(launchd_path)])
            if result.returncode == 0:
                _run_command(["launchctl", "kickstart", "-k", f"{domain}/{_LAUNCHD_SERVICE_NAME}"])
                return True
        raise UpdateRuntimeError(
            error_code="update_failed",
            reason="failed to restart managed host runtime service",
        )
    return False


def _wait_for_local_health(*, settings: Settings, timeout_sec: float = 90.0) -> bool:
    host, runtime_port = _resolve_runtime_health_target(settings)
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    url = f"http://{host}:{runtime_port + _DEFAULT_API_PORT_OFFSET}/healthz"
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=5) as response:
                if 200 <= getattr(response, "status", 200) < 300:
                    return True
        except (OSError, URLError):
            time.sleep(1)
    raise UpdateRuntimeError(
        error_code="update_failed",
        reason=f"runtime did not become healthy after restart: {url}",
    )


def _resolve_runtime_health_target(settings: Settings) -> tuple[str, int]:
    """Resolve the local health endpoint from persisted runtime config with env fallback."""

    runtime_config = read_runtime_config(settings)
    host = str(runtime_config.get("runtime_host", settings.runtime_host)).strip() or "127.0.0.1"
    raw_port = runtime_config.get("runtime_port", settings.runtime_port)
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        port = int(settings.runtime_port)
    return host, port


def _run_checked(
    command: list[str],
    *,
    cwd: Path | None = None,
    error_code: str,
    fallback: str,
) -> subprocess.CompletedProcess[str]:
    result = _run_command(command, cwd=cwd)
    if result.returncode == 0:
        return result
    reason = _command_reason(result, fallback=fallback)
    raise UpdateRuntimeError(error_code=error_code, reason=reason)


def _run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=None if cwd is None else str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )


def _command_reason(
    result: subprocess.CompletedProcess[str],
    *,
    fallback: str,
) -> str:
    stderr = " ".join(result.stderr.strip().split())
    if stderr:
        return stderr
    stdout = " ".join(result.stdout.strip().split())
    if stdout:
        return stdout
    return fallback


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""
