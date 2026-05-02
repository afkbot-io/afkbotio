"""Trusted host executable resolution for security-sensitive tool launches."""

from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import stat


class TrustedExecutableError(RuntimeError):
    """Raised when an executable cannot be resolved through trusted host paths."""


def trusted_system_command_dirs() -> tuple[Path, ...]:
    """Return host command directories considered for direct command allowlists."""

    candidates = (
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
    )
    return tuple(path for path in candidates if path.is_dir())


def trusted_sandbox_helper_dirs() -> tuple[Path, ...]:
    """Return host helper directories considered for OS sandbox binaries."""

    candidates = (
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
    )
    return tuple(path for path in candidates if path.is_dir())


def trusted_path_env() -> str:
    """Return a PATH value containing only trusted root-owned system directories."""

    resolved_dirs: list[str] = []
    seen: set[str] = set()
    for directory in trusted_system_command_dirs():
        try:
            resolved = directory.resolve(strict=True)
        except OSError:
            continue
        if not _trusted_directory_path_is_safe(resolved):
            continue
        value = str(resolved)
        if value in seen:
            continue
        seen.add(value)
        resolved_dirs.append(value)
    return os.pathsep.join(resolved_dirs)


def resolve_trusted_executable(
    names: Iterable[str],
    *,
    candidate_dirs: tuple[Path, ...] | None = None,
) -> Path:
    """Resolve one executable from trusted root-owned paths without consulting PATH."""

    normalized_names = tuple(name for name in (str(raw).strip() for raw in names) if name)
    if not normalized_names:
        raise TrustedExecutableError("No executable names were provided")

    search_dirs = candidate_dirs if candidate_dirs is not None else trusted_system_command_dirs()
    for name in normalized_names:
        if Path(name).name != name:
            continue
        for directory in search_dirs:
            candidate = directory / name
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if _trusted_executable_path_is_safe(resolved):
                return resolved
    allowed = ", ".join(normalized_names)
    raise TrustedExecutableError(
        f"Trusted executable not found for: {allowed}. Install it into a root-owned, "
        "non-writable system directory or disable the feature for trusted profiles."
    )


def is_trusted_executable_path(path: Path) -> bool:
    """Return whether an explicit executable path is root-owned and non-writable."""

    return _trusted_executable_path_is_safe(path)


def _trusted_executable_path_is_safe(path: Path) -> bool:
    """Return whether a resolved executable and its parents are root-owned and non-writable."""

    if os.name != "posix":
        return False
    try:
        current = path.resolve(strict=True)
        file_stat = current.stat()
    except OSError:
        return False
    if not stat.S_ISREG(file_stat.st_mode) or not os.access(current, os.X_OK):
        return False
    if not _trusted_mode_and_owner(file_stat.st_mode, file_stat.st_uid):
        return False

    return _trusted_directory_path_is_safe(current.parent)


def _trusted_directory_path_is_safe(path: Path) -> bool:
    """Return whether a directory and all its parents are root-owned and non-writable."""

    try:
        current = path.resolve(strict=True)
    except OSError:
        return False
    for parent in (current, *current.parents):
        try:
            parent_stat = parent.stat()
        except OSError:
            return False
        if not stat.S_ISDIR(parent_stat.st_mode):
            return False
        if not _trusted_mode_and_owner(parent_stat.st_mode, parent_stat.st_uid):
            return False
        if parent == parent.parent:
            break
    return True


def _trusted_mode_and_owner(mode: int, uid: int) -> bool:
    """Root-owned files/dirs with no group/world write are trusted."""

    writable_by_others = stat.S_IWGRP | stat.S_IWOTH
    return uid == 0 and mode & writable_by_others == 0
