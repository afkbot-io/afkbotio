"""Helpers for mapping high-level file access mode to effective policy fields."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

FILE_READ_ONLY_TOOL_NAMES = frozenset({"file.list", "file.read", "file.search"})
FILE_MUTATING_TOOL_NAMES = frozenset({"file.write", "file.edit"})
FILE_TOOL_NAMES = frozenset((*FILE_READ_ONLY_TOOL_NAMES, *FILE_MUTATING_TOOL_NAMES))
PolicyWorkspaceScopeMode = Literal["profile_only", "project_only", "profile_and_project", "full_system", "custom"]
WORKSPACE_SCOPE_MODE_VALUES: tuple[PolicyWorkspaceScopeMode, ...] = (
    "profile_only",
    "project_only",
    "profile_and_project",
    "full_system",
    "custom",
)


def apply_file_access_mode(
    *,
    allowed_tools: tuple[str, ...],
    file_access_mode: str,
) -> tuple[str, ...]:
    """Filter allowed tools according to high-level file access mode."""

    normalized_mode = file_access_mode.strip().lower()
    if normalized_mode == "read_only":
        return tuple(
            name
            for name in allowed_tools
            if name not in FILE_MUTATING_TOOL_NAMES
        )
    if normalized_mode == "none":
        return tuple(name for name in allowed_tools if name not in FILE_TOOL_NAMES)
    return allowed_tools


def infer_file_access_mode(*, allowed_tools: tuple[str, ...]) -> str:
    """Infer high-level file access mode from the effective allowed-tool set."""

    tool_set = frozenset(allowed_tools)
    if tool_set & FILE_MUTATING_TOOL_NAMES:
        return "read_write"
    if tool_set & FILE_READ_ONLY_TOOL_NAMES:
        return "read_only"
    return "none"


def default_allowed_directories(
    *,
    root_dir: Path,
    profile_root: Path,
    profile_id: str,
) -> tuple[str, ...]:
    """Return recommended file-tool directory scope for one profile."""

    _ = root_dir, profile_id
    return resolve_allowed_directories_for_scope_mode(
        root_dir=root_dir,
        profile_root=profile_root,
        scope_mode="profile_only",
    )


def normalize_workspace_scope_mode(value: str) -> PolicyWorkspaceScopeMode:
    """Validate one high-level workspace scope mode."""

    normalized = value.strip().lower()
    if normalized not in WORKSPACE_SCOPE_MODE_VALUES:
        allowed = ", ".join(WORKSPACE_SCOPE_MODE_VALUES)
        raise ValueError(f"workspace scope mode must be one of: {allowed}")
    return normalized  # type: ignore[return-value]


def resolve_allowed_directories_for_scope_mode(
    *,
    root_dir: Path,
    profile_root: Path,
    scope_mode: str,
    custom_allowed_directories: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Resolve persisted allowed directories from one declarative workspace scope mode."""

    normalized_mode = normalize_workspace_scope_mode(scope_mode)
    resolved_profile_root = (
        profile_root if profile_root.is_absolute() else (root_dir / profile_root)
    ).resolve(strict=False)
    resolved_project_root = root_dir.resolve(strict=False)
    if normalized_mode == "full_system":
        return (str(Path("/").resolve(strict=False)),)
    if normalized_mode == "project_only":
        return (str(resolved_project_root),)
    if normalized_mode == "profile_and_project":
        roots = (resolved_profile_root, resolved_project_root)
        return _normalize_allowed_directories(roots)
    if normalized_mode == "custom":
        custom_roots = tuple(Path(item).expanduser().resolve(strict=False) for item in custom_allowed_directories if item.strip())
        return _normalize_allowed_directories((resolved_profile_root, *custom_roots))
    return (str(resolved_profile_root),)


def infer_workspace_scope_mode(
    *,
    root_dir: Path,
    profile_root: Path,
    allowed_directories: tuple[str, ...],
) -> PolicyWorkspaceScopeMode:
    """Infer one high-level workspace scope mode from persisted directory allowlist."""

    resolved_dirs = tuple(
        Path(item).expanduser().resolve(strict=False)
        for item in allowed_directories
        if str(item).strip()
    )
    if not resolved_dirs:
        return "profile_only"

    unique_dirs = tuple(dict.fromkeys(str(path) for path in resolved_dirs))
    normalized_dirs = tuple(Path(item) for item in unique_dirs)
    resolved_profile_root = (
        profile_root if profile_root.is_absolute() else (root_dir / profile_root)
    ).resolve(strict=False)
    resolved_project_root = root_dir.resolve(strict=False)
    if any(path == Path("/").resolve(strict=False) for path in normalized_dirs):
        return "full_system"
    if normalized_dirs == (resolved_profile_root,):
        return "profile_only"
    if normalized_dirs == (resolved_project_root,):
        return "project_only"
    if set(normalized_dirs) == {resolved_profile_root, resolved_project_root}:
        return "profile_and_project"
    return "custom"


def _normalize_allowed_directories(paths: tuple[Path, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for path in paths:
        marker = str(path.resolve(strict=False))
        if marker in seen:
            continue
        seen.add(marker)
        normalized.append(marker)
    return tuple(normalized)
