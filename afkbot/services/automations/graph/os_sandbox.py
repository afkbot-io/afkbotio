"""OS-level sandbox wrappers for code graph nodes."""

from __future__ import annotations

import logging
import shutil
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from afkbot.settings import Settings


_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CodeNodeLaunch:
    """Launch command prepared for one code-node worker process."""

    argv: tuple[str, ...]
    sandbox_kind: Literal["none", "macos-sandbox-exec"]
    profile_path: Path | None = None


class OSSandboxUnavailableError(RuntimeError):
    """Raised when the configured host-level sandbox policy cannot be satisfied."""


def sandbox_exec_available() -> bool:
    """Return whether `sandbox-exec` can be used on this host."""

    return sys.platform == "darwin" and shutil.which("sandbox-exec") is not None


def build_code_node_launch(
    *,
    base_argv: tuple[str, ...],
    sandbox_root: Path,
    explicit_read_roots: tuple[Path, ...] = (),
    settings: Settings,
) -> CodeNodeLaunch:
    """Wrap the worker command in one host-level sandbox when supported."""

    sandbox_mode = settings.automation_graph_code_os_sandbox
    if sandbox_mode == "disabled":
        return CodeNodeLaunch(argv=base_argv, sandbox_kind="none")
    if not sandbox_exec_available():
        if sandbox_mode == "required":
            raise OSSandboxUnavailableError("OS sandbox is required but unavailable on this host")
        _LOGGER.warning(
            "automation_graph_os_sandbox_auto_fallback configured_mode=auto sandbox_kind=none "
            "reason=sandbox_exec_unavailable platform=%s",
            sys.platform,
        )
        return CodeNodeLaunch(argv=base_argv, sandbox_kind="none")
    sandbox_root = sandbox_root.resolve(strict=False)
    profile_path = sandbox_root / "macos-sandbox.sb"
    profile_path.write_text(
        _build_macos_profile(
            sandbox_root=sandbox_root,
            explicit_read_roots=explicit_read_roots,
        ),
        encoding="utf-8",
    )
    sandbox_exec = shutil.which("sandbox-exec")
    if sandbox_exec is None:  # pragma: no cover - guarded by sandbox_exec_available
        return CodeNodeLaunch(argv=base_argv, sandbox_kind="none")
    return CodeNodeLaunch(
        argv=(sandbox_exec, "-f", str(profile_path), *base_argv),
        sandbox_kind="macos-sandbox-exec",
        profile_path=profile_path,
    )


def _build_macos_profile(*, sandbox_root: Path, explicit_read_roots: tuple[Path, ...]) -> str:
    read_roots = _macos_read_roots(
        sandbox_root=sandbox_root,
        explicit_read_roots=explicit_read_roots,
    )
    executable_literals = _macos_executable_literals()
    lines = [
        "(version 1)",
        "(deny default)",
        '(import "system.sb")',
        _sbpl_allow_literals("process-exec", executable_literals),
        _sbpl_allow("file-read*", read_roots),
        _sbpl_allow("file-write*", (sandbox_root,)),
        "(deny network*)",
    ]
    return "\n".join(lines) + "\n"


def _macos_read_roots(
    *,
    sandbox_root: Path,
    explicit_read_roots: tuple[Path, ...],
) -> tuple[Path, ...]:
    roots: set[Path] = {
        sandbox_root,
        Path("/System"),
    }
    executable = Path(sys.executable)
    roots.add(executable.parent)
    roots.add(executable.resolve(strict=False).parent)
    for prefix in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix):
        raw_prefix = Path(prefix)
        roots.add(raw_prefix)
        roots.add(raw_prefix.resolve(strict=False))
    for raw_path in sysconfig.get_paths().values():
        path = Path(raw_path)
        roots.add(path)
        roots.add(path.resolve(strict=False))
    for path in explicit_read_roots:
        roots.add(path)
        roots.add(path.resolve(strict=False))
    ordered = sorted(
        (path for path in roots if path.exists()),
        key=lambda item: (len(item.parts), str(item)),
    )
    return tuple(ordered)


def _macos_executable_literals() -> tuple[Path, ...]:
    executable = Path(sys.executable)
    literals = {executable, executable.resolve(strict=False)}
    return tuple(sorted((path for path in literals if path.exists()), key=str))




def _sbpl_allow(operation: str, roots: tuple[Path, ...]) -> str:
    entries = " ".join(f'(subpath "{_escape_sbpl(str(path))}")' for path in roots)
    return f"(allow {operation} {entries})"


def _sbpl_allow_literals(operation: str, paths: tuple[Path, ...]) -> str:
    entries = " ".join(f'(literal "{_escape_sbpl(str(path))}")' for path in paths)
    return f"(allow {operation} {entries})"


def _escape_sbpl(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
