"""Host-level shell sandbox launch helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
from typing import Literal

from afkbot.services.policy import (
    normalize_shell_sandbox_mode,
    scope_requires_shell_sandbox,
)
from afkbot.services.tools.trusted_executables import (
    TrustedExecutableError,
    resolve_trusted_executable,
    trusted_sandbox_helper_dirs,
)

ShellSandboxKind = Literal["none", "linux-bwrap", "macos-sandbox-exec"]


class ShellSandboxUnavailableError(RuntimeError):
    """Raised when a required shell sandbox cannot be built on this host."""


@dataclass(frozen=True, slots=True)
class ShellSandboxLaunch:
    """Prepared process launch for one shell command."""

    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    sandbox_kind: ShellSandboxKind
    profile_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ShellSandboxBackendStatus:
    """Detected host support for OS-level shell sandboxing."""

    ok: bool
    sandbox_kind: ShellSandboxKind
    helper_path: str | None
    reason: str
    install_command: tuple[str, ...] = ()


def build_shell_sandbox_launch(
    *,
    base_argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    scope_roots: tuple[Path, ...],
    shell_sandbox_mode: str,
) -> ShellSandboxLaunch:
    """Wrap a shell command in an OS sandbox when the policy requires one."""

    mode = normalize_shell_sandbox_mode(shell_sandbox_mode)
    if mode == "disabled" or not scope_requires_shell_sandbox(scope_roots):
        return ShellSandboxLaunch(argv=base_argv, cwd=cwd, env=env, sandbox_kind="none")

    sandbox_roots = _normalize_scope_roots(scope_roots)
    if sys.platform == "linux":
        launch = _build_linux_bwrap_launch(
            base_argv=base_argv,
            cwd=cwd,
            env=env,
            sandbox_roots=sandbox_roots,
        )
        if launch is not None:
            return launch
    if sys.platform == "darwin":
        launch = _build_macos_sandbox_exec_launch(
            base_argv=base_argv,
            cwd=cwd,
            env=env,
            sandbox_roots=sandbox_roots,
        )
        if launch is not None:
            return launch

    if mode == "required":
        raise ShellSandboxUnavailableError(
            "Shell sandbox is required for this profile, but no supported OS sandbox backend "
            "is available. Install bubblewrap on Linux or use sandbox-exec on macOS, or change "
            "`policy_shell_sandbox_mode` to `best_effort`/`disabled` for a trusted profile."
        )
    return ShellSandboxLaunch(argv=base_argv, cwd=cwd, env=env, sandbox_kind="none")


def get_shell_sandbox_backend_status() -> ShellSandboxBackendStatus:
    """Return whether the host has a trusted shell sandbox backend."""

    if sys.platform == "linux":
        try:
            helper = resolve_trusted_executable(
                ("bwrap", "bubblewrap"),
                candidate_dirs=trusted_sandbox_helper_dirs(),
            )
        except TrustedExecutableError:
            command = _linux_bubblewrap_install_command()
            return ShellSandboxBackendStatus(
                ok=False,
                sandbox_kind="none",
                helper_path=None,
                reason="bubblewrap is not installed in a trusted root-owned system directory",
                install_command=command,
            )
        return ShellSandboxBackendStatus(
            ok=True,
            sandbox_kind="linux-bwrap",
            helper_path=str(helper),
            reason="bubblewrap is available",
        )
    if sys.platform == "darwin":
        try:
            helper = resolve_trusted_executable(
                ("sandbox-exec",),
                candidate_dirs=trusted_sandbox_helper_dirs(),
            )
        except TrustedExecutableError:
            return ShellSandboxBackendStatus(
                ok=False,
                sandbox_kind="none",
                helper_path=None,
                reason="sandbox-exec is not available in a trusted system directory",
            )
        return ShellSandboxBackendStatus(
            ok=True,
            sandbox_kind="macos-sandbox-exec",
            helper_path=str(helper),
            reason="sandbox-exec is available",
        )
    return ShellSandboxBackendStatus(
        ok=False,
        sandbox_kind="none",
        helper_path=None,
        reason="this platform has no supported shell sandbox backend",
    )


def install_shell_sandbox_backend() -> ShellSandboxBackendStatus:
    """Run the detected package-manager install command for the host sandbox backend."""

    status = get_shell_sandbox_backend_status()
    if status.ok or not status.install_command:
        return status
    subprocess.run(status.install_command, check=False)  # noqa: S603
    return get_shell_sandbox_backend_status()


def _build_linux_bwrap_launch(
    *,
    base_argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    sandbox_roots: tuple[Path, ...],
) -> ShellSandboxLaunch | None:
    try:
        bwrap = str(
            resolve_trusted_executable(
                ("bwrap", "bubblewrap"),
                candidate_dirs=trusted_sandbox_helper_dirs(),
            )
        )
    except TrustedExecutableError:
        return None

    first_root = sandbox_roots[0]
    args: list[str] = [
        bwrap,
        "--die-with-parent",
        "--unshare-all",
        "--new-session",
        "--clearenv",
    ]
    for path in _linux_readonly_system_roots():
        args.extend(("--ro-bind", str(path), str(path)))
    args.extend(("--dev", "/dev", "--tmpfs", "/tmp"))
    for root in sandbox_roots:
        args.extend(_bwrap_parent_dir_args(root))
        args.extend(("--bind", str(root), str(root)))
    args.extend(("--dir", "/workspace", "--bind", str(first_root), "/workspace"))
    for key, value in _sandbox_env(env=env, home="/workspace").items():
        args.extend(("--setenv", key, value))
    args.extend(("--chdir", str(cwd), *base_argv))
    return ShellSandboxLaunch(
        argv=tuple(args),
        cwd=first_root,
        env={},
        sandbox_kind="linux-bwrap",
    )


def _build_macos_sandbox_exec_launch(
    *,
    base_argv: tuple[str, ...],
    cwd: Path,
    env: dict[str, str],
    sandbox_roots: tuple[Path, ...],
) -> ShellSandboxLaunch | None:
    try:
        sandbox_exec = str(
            resolve_trusted_executable(
                ("sandbox-exec",),
                candidate_dirs=trusted_sandbox_helper_dirs(),
            )
        )
    except TrustedExecutableError:
        return None
    profile = _build_macos_profile(sandbox_roots=sandbox_roots)
    return ShellSandboxLaunch(
        argv=(sandbox_exec, "-p", profile, *base_argv),
        cwd=cwd,
        env=_sandbox_env(env=env, home=str(sandbox_roots[0])),
        sandbox_kind="macos-sandbox-exec",
    )


def _normalize_scope_roots(scope_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[str] = set()
    for root in scope_roots:
        resolved = root.resolve(strict=False)
        marker = str(resolved)
        if marker in seen:
            continue
        seen.add(marker)
        roots.append(resolved)
    if not roots:
        raise ShellSandboxUnavailableError("Shell sandbox requires at least one scope root")
    return tuple(roots)


def _linux_readonly_system_roots() -> tuple[Path, ...]:
    candidates = (
        Path("/bin"),
        Path("/sbin"),
        Path("/usr"),
        Path("/lib"),
        Path("/lib64"),
        Path("/opt/homebrew"),
    )
    return tuple(path for path in candidates if path.exists())


def _linux_bubblewrap_install_command() -> tuple[str, ...]:
    managers: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("apt-get", ("sudo", "apt-get", "install", "-y", "bubblewrap")),
        ("dnf", ("sudo", "dnf", "install", "-y", "bubblewrap")),
        ("yum", ("sudo", "yum", "install", "-y", "bubblewrap")),
        ("zypper", ("sudo", "zypper", "install", "-y", "bubblewrap")),
        ("pacman", ("sudo", "pacman", "-S", "--noconfirm", "bubblewrap")),
        ("apk", ("sudo", "apk", "add", "bubblewrap")),
    )
    for executable, command in managers:
        if shutil.which(executable):
            return command
    return ()


def _bwrap_parent_dir_args(path: Path) -> tuple[str, ...]:
    parts = path.resolve(strict=False).parts
    if len(parts) <= 2:
        return ()
    args: list[str] = []
    current = Path(parts[0])
    for part in parts[1:-1]:
        current = current / part
        args.extend(("--dir", str(current)))
    return tuple(args)


def _sandbox_env(*, env: dict[str, str], home: str) -> dict[str, str]:
    sandboxed = {
        key: str(value)
        for key, value in env.items()
        if key not in {"HOME", "TMPDIR", "TMP", "TEMP"}
    }
    sandboxed["HOME"] = home
    sandboxed["TMPDIR"] = "/tmp"
    sandboxed["TMP"] = "/tmp"
    sandboxed["TEMP"] = "/tmp"
    sandboxed.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return sandboxed


def _build_macos_profile(*, sandbox_roots: tuple[Path, ...]) -> str:
    read_roots = _macos_read_roots(sandbox_roots=sandbox_roots)
    write_roots = " ".join(f'(subpath "{_escape_sbpl(str(root))}")' for root in sandbox_roots)
    read_entries = " ".join(f'(subpath "{_escape_sbpl(str(root))}")' for root in read_roots)
    return "\n".join(
        (
            "(version 1)",
            "(deny default)",
            '(import "system.sb")',
            "(allow process-exec)",
            f"(allow file-read* {read_entries})",
            f"(allow file-write* {write_roots} (subpath \"/tmp\"))",
            "(deny network*)",
        )
    ) + "\n"


def _macos_read_roots(*, sandbox_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    roots: set[Path] = {Path("/System"), Path("/bin"), Path("/usr/bin"), Path("/usr/lib")}
    roots.update(sandbox_roots)
    executable = Path(sys.executable)
    roots.add(executable.parent)
    roots.add(executable.resolve(strict=False).parent)
    for prefix in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix):
        roots.add(Path(prefix))
        roots.add(Path(prefix).resolve(strict=False))
    for raw_path in sysconfig.get_paths().values():
        path = Path(raw_path)
        roots.add(path)
        roots.add(path.resolve(strict=False))
    return tuple(sorted((path for path in roots if path.exists()), key=str))


def _escape_sbpl(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
