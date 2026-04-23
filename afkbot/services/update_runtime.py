"""Update helpers for source checkouts, legacy managed installs, and uv tool installs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from packaging.version import InvalidVersion, Version
import re
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from afkbot.services.install_source import (
    INSTALL_SOURCE_MODE_ENV,
    INSTALL_SOURCE_RESOLVED_TARGET_ENV,
    INSTALL_SOURCE_SPEC_ENV,
    InstallSource,
    build_uv_tool_install_command,
    default_package_install_source,
    read_install_source_from_runtime_config,
    read_install_source_resolved_target_from_runtime_config,
)
from afkbot.services.managed_runtime_service import ensure_managed_runtime_service
from afkbot.services.managed_install import (
    ManagedInstallContext,
    build_next_app_dir,
    cleanup_staged_source,
    prune_stale_app_dirs,
    resolve_managed_install_context,
    stage_source_snapshot,
    write_managed_launcher,
)
from afkbot.services.setup.state import setup_is_complete
from afkbot.services.setup.runtime_store import read_runtime_config
from afkbot.services.runtime_ports import resolve_default_runtime_port
from afkbot.settings import Settings, get_settings
from afkbot.version import load_cli_version_info

_DEFAULT_API_PORT_OFFSET = 1
_CODE_CHECKOUT_ROOT = Path(__file__).resolve(strict=False).parents[2]
_GIT_NETWORK_TIMEOUT_SEC = 10.0
_SSH_BATCHMODE_OPTION_RE = re.compile(
    r"BatchMode\s*(?:=\s*)?(?:yes|no|true|false)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """Summary of one local-runtime update run."""

    install_mode: str
    source_updated: bool
    runtime_restarted: bool
    maintenance_applied: bool
    details: tuple[str, ...]
    source_status: str | None = None


@dataclass(frozen=True, slots=True)
class UpdateAvailability:
    """Summary of one available update without mutating local install state."""

    install_mode: str
    current_version: str
    target_id: str
    target_label: str
    details: tuple[str, ...]


class UpdateRuntimeError(RuntimeError):
    """Raised when managed runtime update cannot complete."""

    def __init__(self, *, error_code: str, reason: str) -> None:
        super().__init__(reason)
        self.error_code = error_code
        self.reason = reason


def inspect_available_update(settings: Settings) -> UpdateAvailability | None:
    """Return one available update summary when the current install can detect one."""

    try:
        runtime_config = read_runtime_config(settings)
        managed_context = resolve_managed_install_context()
        if managed_context is not None:
            return _inspect_managed_update(context=managed_context)
        install_source = read_install_source_from_runtime_config(runtime_config)
        if install_source is not None:
            return _inspect_installer_source_update(
                install_source=install_source,
                runtime_config=runtime_config,
            )
        if _is_source_checkout_install():
            return _inspect_host_update(settings=settings)
        return _inspect_uv_tool_update(runtime_config=runtime_config)
    except UpdateRuntimeError:
        return None
    except (OSError, ValueError, URLError, json.JSONDecodeError):
        return None


def run_update(settings: Settings) -> UpdateResult:
    """Update the local AFKBOT source checkout and apply pending maintenance."""

    runtime_config = read_runtime_config(settings)
    managed_context = resolve_managed_install_context()
    if managed_context is not None:
        return _run_managed_update(settings=settings, context=managed_context)
    install_source = read_install_source_from_runtime_config(runtime_config)
    if install_source is not None:
        return _run_installer_source_update(
            settings=settings,
            install_source=install_source,
            runtime_config=runtime_config,
        )
    if _is_source_checkout_install():
        return _run_host_update(settings=settings)
    return _run_uv_tool_update(settings=settings, runtime_config=runtime_config)


def format_update_success(result: UpdateResult) -> str:
    """Render short human-readable update summary."""

    return format_update_success_for_language(result, lang="en")


def format_update_success_for_language(
    result: UpdateResult,
    *,
    lang: str,
) -> str:
    """Render short human-readable update summary for one prompt language."""

    normalized_lang = _normalize_update_language(lang)
    source_status = getattr(result, "source_status", None) or (
        "updated" if result.source_updated else "already_up_to_date"
    )
    lines = [
        "Обновление AFKBOT завершено." if normalized_lang == "ru" else "AFKBOT update complete."
    ]
    lines.append(
        f"Режим установки: {result.install_mode}"
        if normalized_lang == "ru"
        else f"Install mode: {result.install_mode}"
    )
    lines.append(
        "Источник: обновлён"
        if (normalized_lang == "ru" and source_status == "updated")
        else (
            "Источник: уже актуален"
            if (normalized_lang == "ru" and source_status == "already_up_to_date")
            else (
                "Источник: статус не подтверждён"
                if normalized_lang == "ru"
                else (
                    "Source: updated"
                    if source_status == "updated"
                    else (
                        "Source: already up to date"
                        if source_status == "already_up_to_date"
                        else "Source: version status unverified"
                    )
                )
            )
        )
    )
    lines.append(
        "Обслуживание: выполнено"
        if (normalized_lang == "ru" and result.maintenance_applied)
        else (
            "Обслуживание: пропущено"
            if normalized_lang == "ru"
            else ("Maintenance: applied" if result.maintenance_applied else "Maintenance: skipped")
        )
    )
    lines.append(
        "Runtime: перезапущен"
        if (normalized_lang == "ru" and result.runtime_restarted)
        else (
            "Runtime: без managed-перезапуска"
            if normalized_lang == "ru"
            else (
                "Runtime: restarted" if result.runtime_restarted else "Runtime: no managed restart"
            )
        )
    )
    for detail in result.details:
        lines.append(_localize_update_detail(detail, lang=normalized_lang))
    return "\n".join(lines)


def _inspect_host_update(*, settings: Settings) -> UpdateAvailability | None:
    """Check whether a newer commit is available for the current git checkout."""

    project_root = _resolve_host_checkout_root(settings)
    _assert_host_git_checkout(project_root)
    branch = _git_stdout(project_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if not branch or _git_worktree_is_dirty(project_root) or not _git_has_origin(project_root):
        return None
    current_head = _git_stdout(project_root, "rev-parse", "HEAD")
    if not current_head:
        return None
    _run_checked(
        ["git", "-C", str(project_root), "fetch", "--depth", "1", "--no-tags", "origin", branch],
        error_code="update_check_failed",
        fallback=f"failed to fetch latest source for branch {branch}",
        timeout_sec=_GIT_NETWORK_TIMEOUT_SEC,
        env=_build_noninteractive_git_env(),
    )
    fetched_head = _git_stdout(project_root, "rev-parse", "FETCH_HEAD")
    if not fetched_head or fetched_head == current_head:
        return None
    return UpdateAvailability(
        install_mode="host",
        current_version=load_cli_version_info(root_dir=project_root).render(),
        target_id=f"git:{branch}:{fetched_head}",
        target_label=f"origin/{branch} @ {fetched_head[:12]}",
        details=(f"Git branch: {branch}",),
    )


def _inspect_managed_update(*, context: ManagedInstallContext) -> UpdateAvailability | None:
    """Check whether a managed install points to a newer remote revision."""

    parsed = _parse_remote_source_ref(source_url=context.source_url, source_ref=context.source_ref)
    if parsed is None:
        return None
    latest_sha = _fetch_github_commit_sha(owner=parsed.owner, repo=parsed.repo, ref=parsed.ref)
    current_sha = load_cli_version_info(root_dir=context.app_dir).git_sha
    if not latest_sha or not current_sha or latest_sha.startswith(current_sha):
        return None
    return UpdateAvailability(
        install_mode="managed",
        current_version=load_cli_version_info(root_dir=context.app_dir).render(),
        target_id=f"github:{parsed.owner}/{parsed.repo}@{parsed.ref}:{latest_sha}",
        target_label=f"{parsed.owner}/{parsed.repo}@{parsed.ref} @ {latest_sha[:12]}",
        details=(f"Managed source ref: {parsed.ref}",),
    )


def _inspect_uv_tool_update(*, runtime_config: dict[str, object]) -> UpdateAvailability | None:
    """Check whether one legacy uv-tool install has a newer package/archive target."""

    install_source = read_install_source_from_runtime_config(runtime_config)
    if install_source is None:
        install_source = default_package_install_source()
    return _inspect_installer_source_update(
        install_source=install_source,
        runtime_config=runtime_config,
    )


def _inspect_installer_source_update(
    *,
    install_source: InstallSource,
    runtime_config: dict[str, object],
) -> UpdateAvailability | None:
    """Check whether installer-source metadata points to a newer remote release."""

    if install_source.mode == "package":
        current_info = load_cli_version_info()
        current_target = (
            read_install_source_resolved_target_from_runtime_config(runtime_config)
            or current_info.version
        )
        latest_version = resolve_install_source_target(install_source)
        if not latest_version or not _version_is_newer(latest_version, current_target):
            return None
        return UpdateAvailability(
            install_mode="uv-tool",
            current_version=current_info.render(),
            target_id=f"package:{install_source.spec}:{latest_version}",
            target_label=f"{install_source.spec} {latest_version}",
            details=(f"Install source: {install_source.spec}",),
        )
    parsed = _parse_install_source_for_update(install_source)
    if parsed is None:
        return None
    latest_sha = resolve_install_source_target(install_source)
    current_info = load_cli_version_info()
    current_sha = (
        read_install_source_resolved_target_from_runtime_config(runtime_config)
        or current_info.git_sha
    )
    if not latest_sha or not current_sha or latest_sha.startswith(current_sha):
        return None
    return UpdateAvailability(
        install_mode="uv-tool" if install_source.mode == "archive" else install_source.mode,
        current_version=current_info.render(),
        target_id=f"github:{parsed.owner}/{parsed.repo}@{parsed.ref}:{latest_sha}",
        target_label=f"{parsed.owner}/{parsed.repo}@{parsed.ref} @ {latest_sha[:12]}",
        details=(f"Install source: {install_source.spec}",),
    )


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
        _refresh_editable_environment(
            project_root=project_root,
            python_executable=Path(sys.executable),
            fallback="failed to refresh local Python environment",
        )

    _run_afk_subcommand(
        settings=settings,
        args=("doctor", "--no-integrations", "--no-upgrades", "--no-daemon"),
    )
    _run_afk_subcommand(settings=settings, args=("upgrade", "apply", "--quiet"))

    runtime_restarted = _restart_managed_host_runtime_service()
    details = [
        f"Git branch: {branch}",
        (f"Git source reset to origin/{branch} after history rewrite" if history_rewritten else ""),
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
        source_status="updated" if source_updated else "already_up_to_date",
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

    _refresh_editable_environment(
        project_root=next_app_dir,
        python_executable=Path(sys.executable),
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

    _run_afk_subcommand(
        settings=settings,
        args=("doctor", "--no-integrations", "--no-upgrades", "--no-daemon"),
    )
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
        source_status="updated",
        runtime_restarted=runtime_restarted,
        maintenance_applied=True,
        details=tuple(detail for detail in details if detail),
    )


def _run_uv_tool_update(*, settings: Settings, runtime_config: dict[str, object]) -> UpdateResult:
    """Update one uv-installed AFKBOT tool environment and apply maintenance in a new process."""

    install_source = read_install_source_from_runtime_config(runtime_config)
    if install_source is None:
        install_source = default_package_install_source()
    return _run_installer_source_update(
        settings=settings,
        install_source=install_source,
        runtime_config=runtime_config,
    )


def _run_installer_source_update(
    *,
    settings: Settings,
    install_source: InstallSource,
    runtime_config: dict[str, object],
) -> UpdateResult:
    """Replay one installer-style uv tool install and then apply maintenance."""

    uv_executable = _resolve_uv_executable()
    resolved_target = resolve_install_source_target(install_source)
    afk_executable_before = _resolve_uv_tool_afk_executable(uv_executable=uv_executable)
    installed_version_before = _read_afk_executable_version(
        executable=afk_executable_before,
        settings=settings,
    )
    install_command = build_uv_tool_install_command(
        uv_executable=uv_executable,
        install_source=install_source,
    )
    _run_checked(
        install_command,
        error_code="update_failed",
        fallback="failed to reinstall AFKBOT from the saved installer source",
    )
    shell_updated = _update_uv_tool_shell_integration(uv_executable=uv_executable)
    afk_executable = _resolve_uv_tool_afk_executable(uv_executable=uv_executable)
    installed_version_after = _read_afk_executable_version(
        executable=afk_executable,
        settings=settings,
    )
    _run_bootstrap_only_setup(
        executable=afk_executable,
        settings=settings,
        install_source=install_source,
        runtime_config=runtime_config,
    )
    _run_afk_executable(
        executable=afk_executable,
        settings=settings,
        args=("upgrade", "apply", "--quiet"),
    )
    doctor_ran = False
    if setup_is_complete(settings):
        _run_afk_executable(
            executable=afk_executable,
            settings=settings,
            args=("doctor", "--no-integrations", "--no-upgrades", "--no-daemon"),
        )
        doctor_ran = True

    source_status, version_details = _summarize_installer_version_change(
        install_source=install_source,
        installed_version_before=installed_version_before,
        installed_version_after=installed_version_after,
        resolved_target=resolved_target,
    )
    source_updated = source_status == "updated"
    runtime_restarted = _restart_managed_host_runtime_service()
    details = [
        f"Tool source mode: {install_source.mode}",
        f"Tool source: {install_source.spec}",
        f"Tool executable: {afk_executable}",
        *version_details,
        ("Shell integration: refreshed" if shell_updated else "Shell integration: unchanged"),
        "Bootstrap setup: refreshed",
        "Doctor: skipped until `afk setup` completes" if not doctor_ran else "",
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
        source_updated=source_updated,
        source_status=source_status,
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
        timeout_sec=_GIT_NETWORK_TIMEOUT_SEC,
        env=_build_noninteractive_git_env(),
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


def _refresh_editable_environment(
    *,
    project_root: Path,
    python_executable: Path,
    fallback: str,
) -> None:
    """Refresh one editable install in the active Python environment."""

    try:
        uv_executable = _resolve_uv_executable()
    except UpdateRuntimeError:
        _run_checked(
            [str(python_executable), "-m", "pip", "install", "-e", str(project_root)],
            cwd=project_root,
            error_code="update_failed",
            fallback=fallback,
        )
        return
    _run_checked(
        [
            str(uv_executable),
            "pip",
            "install",
            "--python",
            str(python_executable),
            "--editable",
            str(project_root),
        ],
        cwd=project_root,
        error_code="update_failed",
        fallback=fallback,
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
    candidates = (
        (bin_dir / "afk.exe", bin_dir / "afk.cmd") if os.name == "nt" else (bin_dir / "afk",)
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise UpdateRuntimeError(
        error_code="update_failed",
        reason=(
            "AFKBOT executable not found in uv tool bin directory: "
            + ", ".join(str(candidate) for candidate in candidates)
        ),
    )


def _update_uv_tool_shell_integration(*, uv_executable: Path) -> bool:
    """Best-effort replay of the installer shell-integration refresh."""

    result = _run_command([str(uv_executable), "tool", "update-shell"])
    return result.returncode == 0


def _read_afk_executable_version(*, executable: Path, settings: Settings) -> str | None:
    """Return one installed AFKBOT semantic version from the target executable."""

    cwd = _ensure_runtime_command_cwd(settings.root_dir)
    result = _run_command([str(executable), "version"], cwd=cwd)
    if result.returncode != 0:
        return None
    line = next((item.strip() for item in result.stdout.splitlines() if item.strip()), "")
    if not line.startswith("afk "):
        return None
    rendered = line.removeprefix("afk ").strip()
    if not rendered:
        return None
    return rendered.split(" ", 1)[0].split("(", 1)[0].strip() or None


def _summarize_installer_version_change(
    *,
    install_source: InstallSource,
    installed_version_before: str | None,
    installed_version_after: str | None,
    resolved_target: str | None,
) -> tuple[str, tuple[str, ...]]:
    """Return whether the installer really updated AFKBOT plus operator-facing detail lines."""

    details: list[str] = []
    if installed_version_before:
        details.append(f"Installed version before: {installed_version_before}")
    else:
        details.append("Installed version before: unknown")
    if resolved_target:
        target_label = (
            f"Latest package version: {resolved_target}"
            if install_source.mode == "package"
            else f"Resolved source target: {resolved_target}"
        )
        details.append(target_label)
    if installed_version_after:
        details.append(f"Installed version after: {installed_version_after}")
    else:
        details.append("Installed version after: unknown")

    source_updated = bool(
        installed_version_before
        and installed_version_after
        and installed_version_before != installed_version_after
    )
    if source_updated:
        details.append("New version installed")
        return "updated", tuple(details)

    if installed_version_after and resolved_target and installed_version_after == resolved_target:
        details.append("Already on the newest available version")
        return "already_up_to_date", tuple(details)
    if (
        installed_version_before
        and installed_version_after
        and installed_version_before == installed_version_after
    ):
        details.append("No newer version was installed")
        return "already_up_to_date", tuple(details)
    details.append("Version change could not be verified")
    return "unverified", tuple(details)


def _run_bootstrap_only_setup(
    *,
    executable: Path,
    settings: Settings,
    install_source: InstallSource,
    runtime_config: dict[str, object],
) -> None:
    """Replay the installer bootstrap-only setup step after reinstalling the tool."""

    env = dict(os.environ)
    env[INSTALL_SOURCE_MODE_ENV] = install_source.mode
    env[INSTALL_SOURCE_SPEC_ENV] = install_source.spec
    resolved_target = resolve_install_source_target(install_source)
    if resolved_target:
        env[INSTALL_SOURCE_RESOLVED_TARGET_ENV] = resolved_target
    else:
        env.pop(INSTALL_SOURCE_RESOLVED_TARGET_ENV, None)
    _run_afk_executable_with_env(
        executable=executable,
        settings=settings,
        args=(
            "setup",
            "--bootstrap-only",
            "--yes",
            "--lang",
            _resolve_bootstrap_setup_lang(runtime_config=runtime_config),
        ),
        env=env,
    )


def _resolve_bootstrap_setup_lang(*, runtime_config: dict[str, object]) -> str:
    """Return the setup language used when replaying installer bootstrap state."""

    configured = str(runtime_config.get("prompt_language") or "").strip().lower()
    if configured in {"en", "ru"}:
        return configured
    locale_candidates = (
        str(os.getenv("LC_ALL") or "").strip().lower(),
        str(os.getenv("LC_MESSAGES") or "").strip().lower(),
        str(os.getenv("LANG") or "").strip().lower(),
    )
    for candidate in locale_candidates:
        if candidate.startswith("ru"):
            return "ru"
    return "en"


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
        [
            "git",
            "-C",
            str(project_root),
            "diff",
            "--cached",
            "--quiet",
            "--ignore-submodules",
            "--",
        ],
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
    cwd = _ensure_runtime_command_cwd(settings.root_dir)
    _run_checked(
        [sys.executable, "-m", "afkbot.cli.main", *args],
        cwd=cwd,
        error_code="update_failed",
        fallback=f"failed to run AFKBOT command: {' '.join(args)}",
    )


def _run_afk_executable(*, executable: Path, settings: Settings, args: tuple[str, ...]) -> None:
    cwd = _ensure_runtime_command_cwd(settings.root_dir)
    _run_checked(
        [str(executable), *args],
        cwd=cwd,
        error_code="update_failed",
        fallback=f"failed to run AFKBOT command: {' '.join(args)}",
    )


def _run_afk_executable_with_env(
    *,
    executable: Path,
    settings: Settings,
    args: tuple[str, ...],
    env: dict[str, str],
) -> None:
    cwd = _ensure_runtime_command_cwd(settings.root_dir)
    result = subprocess.run(
        [str(executable), *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode == 0:
        return
    raise UpdateRuntimeError(
        error_code="update_failed",
        reason=_command_reason(
            result,
            fallback=f"failed to run AFKBOT command: {' '.join(args)}",
        ),
    )


def _ensure_runtime_command_cwd(root_dir: Path) -> Path:
    """Create the runtime working directory on demand before spawning AFKBOT subprocesses."""

    root_dir.mkdir(parents=True, exist_ok=True)
    return root_dir


def _restart_managed_host_runtime_service(settings: Settings | None = None) -> bool:
    resolved_settings = settings or get_settings()
    result = ensure_managed_runtime_service(
        resolved_settings,
        start=setup_is_complete(resolved_settings),
    )
    if result.status == "failed":
        raise UpdateRuntimeError(
            error_code="update_failed",
            reason=result.reason or "failed to restart managed host runtime service",
        )
    return result.status in {"installed", "restarted"}


def _wait_for_local_health(*, settings: Settings, timeout_sec: float = 90.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        host, runtime_port = _resolve_runtime_health_target(settings)
        if host in {"0.0.0.0", "::"}:
            host = "127.0.0.1"
        url = f"http://{host}:{runtime_port + _DEFAULT_API_PORT_OFFSET}/healthz"
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
    port = resolve_default_runtime_port(
        settings=settings,
        host=host,
        runtime_config=runtime_config,
    )
    return host, port


@dataclass(frozen=True, slots=True)
class _RemoteSourceRef:
    owner: str
    repo: str
    ref: str


def _parse_install_source_for_update(install_source: InstallSource) -> _RemoteSourceRef | None:
    """Parse one installer source into a GitHub repository ref when possible."""

    if install_source.mode == "editable":
        return None
    if install_source.spec.startswith("github:"):
        raw_repo = install_source.spec.removeprefix("github:")
        repo_spec, sep, ref = raw_repo.partition("@")
        owner, slash, repo = repo_spec.partition("/")
        if not slash or not owner or not repo or not _is_trackable_ref(ref or "main"):
            return None
        return _RemoteSourceRef(owner=owner, repo=repo, ref=ref or "main")
    parsed = urlparse(install_source.spec)
    if parsed.netloc != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[2] != "archive":
        return None
    owner, repo = parts[0], parts[1]
    ref = parts[-1]
    if ref.endswith(".tar.gz"):
        ref = ref[: -len(".tar.gz")]
    if ref == "main.tar":
        ref = "main"
    if ref == "master.tar":
        ref = "master"
    if not owner or not repo or not _is_trackable_ref(ref):
        return None
    return _RemoteSourceRef(owner=owner, repo=repo, ref=ref)


def _parse_remote_source_ref(*, source_url: str, source_ref: str) -> _RemoteSourceRef | None:
    """Parse one managed remote source into a GitHub repository ref when possible."""

    parsed = urlparse(source_url)
    if parsed.netloc != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or not _is_trackable_ref(source_ref):
        return None
    return _RemoteSourceRef(owner=parts[0], repo=parts[1], ref=source_ref)


def _is_trackable_ref(ref: str) -> bool:
    """Return whether one ref should participate in auto-update prompting."""

    normalized = ref.strip()
    return normalized in {"main", "master"}


def _fetch_github_commit_sha(*, owner: str, repo: str, ref: str) -> str | None:
    """Return the latest commit SHA for one GitHub branch ref."""

    payload = _fetch_json_payload(
        f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}",
    )
    sha = str(payload.get("sha") or "").strip()
    return sha or None


def resolve_install_source_target(install_source: InstallSource) -> str | None:
    """Resolve one stable target marker for installer-style update comparisons."""

    try:
        if install_source.mode == "package":
            payload = _fetch_json_payload(f"https://pypi.org/pypi/{install_source.spec}/json")
            version = str(_json_object_field(payload, "info").get("version") or "").strip()
            return version or None
        parsed = _parse_install_source_for_update(install_source)
        if parsed is None:
            return None
        return _fetch_github_commit_sha(owner=parsed.owner, repo=parsed.repo, ref=parsed.ref)
    except (OSError, ValueError, URLError, json.JSONDecodeError):
        return None


def _fetch_json_payload(url: str) -> dict[str, object]:
    """Fetch one JSON object payload from a remote endpoint."""

    request = Request(url, headers={"User-Agent": "afkbot/update-check"})
    with urlopen(request, timeout=10) as response:
        raw = response.read().decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Expected JSON object payload")
    return payload


def _json_object_field(payload: dict[str, object], key: str) -> dict[str, object]:
    """Return one JSON object field as a typed mapping when present."""

    value = payload.get(key)
    if not isinstance(value, dict):
        return {}
    return {str(field_key): field_value for field_key, field_value in value.items()}


def _version_is_newer(candidate: str, current: str) -> bool:
    """Return whether one candidate package version is newer than current."""

    try:
        return Version(candidate) > Version(current)
    except InvalidVersion:
        return candidate.strip() != current.strip()


def _normalize_update_language(value: str) -> str:
    normalized = str(value).strip().lower()
    return "ru" if normalized.startswith("ru") else "en"


def _localize_update_detail(detail: str, *, lang: str) -> str:
    if lang != "ru":
        return detail
    if detail.startswith("Git branch: "):
        return f"Git-ветка: {detail.removeprefix('Git branch: ')}"
    if detail.startswith("Git source reset to origin/"):
        return (
            "Git checkout сброшен на "
            + detail.removeprefix("Git source reset to ").removesuffix(" after history rewrite")
            + " после переписанной истории"
        )
    if detail == "Runtime health: ok":
        return "Состояние runtime: ok"
    if detail == "Managed host service not found; restart manually with `afk start`":
        return "Managed host service не найден; перезапустите вручную через `afk start`"
    if detail.startswith("Managed source ref: "):
        return f"Managed source ref: {detail.removeprefix('Managed source ref: ')}"
    if detail.startswith("Managed source dir: "):
        return f"Каталог managed-source: {detail.removeprefix('Managed source dir: ')}"
    if detail.startswith("Tool source mode: "):
        return f"Режим источника tool: {detail.removeprefix('Tool source mode: ')}"
    if detail.startswith("Tool source: "):
        return f"Источник tool: {detail.removeprefix('Tool source: ')}"
    if detail.startswith("Tool executable: "):
        return f"Исполняемый файл tool: {detail.removeprefix('Tool executable: ')}"
    if detail.startswith("Installed version before: "):
        return f"Версия до обновления: {detail.removeprefix('Installed version before: ')}"
    if detail.startswith("Installed version after: "):
        return f"Версия после обновления: {detail.removeprefix('Installed version after: ')}"
    if detail.startswith("Latest package version: "):
        return f"Последняя версия пакета: {detail.removeprefix('Latest package version: ')}"
    if detail.startswith("Resolved source target: "):
        return f"Разрешённая цель источника: {detail.removeprefix('Resolved source target: ')}"
    if detail == "Shell integration: refreshed":
        return "Интеграция shell: обновлена"
    if detail == "Shell integration: unchanged":
        return "Интеграция shell: без изменений"
    if detail == "Bootstrap setup: refreshed":
        return "Bootstrap-настройка: обновлена"
    if detail == "New version installed":
        return "Установлена новая версия"
    if detail == "Already on the newest available version":
        return "Уже установлена последняя доступная версия"
    if detail == "No newer version was installed":
        return "Новая версия не была установлена"
    if detail == "Version change could not be verified":
        return "Не удалось подтвердить изменение версии"
    return detail


def _build_noninteractive_git_env() -> dict[str, str]:
    """Return a git environment that fails fast instead of prompting on chat startup."""

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env["GIT_SSH_COMMAND"] = _force_git_ssh_batch_mode(
        str(env.get("GIT_SSH_COMMAND") or "").strip()
    )
    return env


def _force_git_ssh_batch_mode(command: str) -> str:
    """Return a GIT_SSH_COMMAND value that disables interactive SSH prompts."""

    if not command:
        return "ssh -oBatchMode=yes"
    if _SSH_BATCHMODE_OPTION_RE.search(command):
        return _SSH_BATCHMODE_OPTION_RE.sub("BatchMode=yes", command)
    return f"{command} -oBatchMode=yes"


def _run_checked(
    command: list[str],
    *,
    cwd: Path | None = None,
    error_code: str,
    fallback: str,
    timeout_sec: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = _run_command(command, cwd=cwd, timeout_sec=timeout_sec, env=env)
    except subprocess.TimeoutExpired as exc:
        timeout_label = (
            f" timed out after {timeout_sec:g} seconds" if timeout_sec is not None else " timed out"
        )
        raise UpdateRuntimeError(
            error_code=error_code,
            reason=f"{fallback}; command{timeout_label}",
        ) from exc
    if result.returncode == 0:
        return result
    reason = _command_reason(result, fallback=fallback)
    raise UpdateRuntimeError(error_code=error_code, reason=reason)


def _run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_sec: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=None if cwd is None else str(cwd),
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        env=env,
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
