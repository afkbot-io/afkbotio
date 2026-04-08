"""Workspace helpers shared by file and shell tools."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.profile_runtime.service import ProfileServiceError, get_profile_service
from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.path_scope import resolve_in_scope
from afkbot.settings import Settings


class WorkspacePathResolutionError(ValueError):
    """Structured workspace path failure for scope and existence errors."""

    def __init__(self, *, code: str, raw_path: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.raw_path = raw_path
        self.reason = reason


def resolve_tool_workspace_base_dir(*, settings: Settings, profile_id: str) -> Path:
    """Return default base directory for relative tool paths in one profile."""

    if settings.tool_workspace_root is not None:
        return settings.tool_workspace_dir
    if settings.tool_invocation_cwd is not None:
        base_dir = settings.tool_invocation_cwd
        if not base_dir.is_absolute():
            base_dir = settings.root_dir / base_dir
        return base_dir.resolve(strict=False)
    base_dir = get_profile_runtime_config_service(settings).profile_root(profile_id)
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


async def resolve_tool_workspace_scope_roots(*, settings: Settings, profile_id: str) -> tuple[Path, ...]:
    """Return hard scope roots enforced by tool runtime for one profile."""

    base_dir = resolve_tool_workspace_base_dir(settings=settings, profile_id=profile_id)
    if settings.tool_workspace_root is None:
        try:
            profile = await get_profile_service(settings).get(profile_id=profile_id)
        except ProfileServiceError:
            return (base_dir,)
        if not profile.policy.enabled:
            return ()
        resolved_roots = _normalize_scope_roots(profile.policy.allowed_directories)
        if not resolved_roots:
            return (base_dir,)
        if not any(base_dir.is_relative_to(scope_root) for scope_root in resolved_roots):
            return _prepend_scope_root(primary=base_dir, roots=resolved_roots)
        return resolved_roots
    return (settings.tool_workspace_dir.resolve(strict=False),)


def _normalize_scope_roots(raw_roots: tuple[str, ...]) -> tuple[Path, ...]:
    roots: list[Path] = []
    seen: set[str] = set()
    for raw_root in raw_roots:
        normalized = raw_root.strip()
        if not normalized:
            continue
        resolved = Path(normalized).expanduser().resolve(strict=False)
        marker = str(resolved)
        if marker in seen:
            continue
        seen.add(marker)
        roots.append(resolved)
    return tuple(roots)


def _prepend_scope_root(*, primary: Path, roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Preserve the profile workspace as default cwd while keeping broader scope roots."""

    return _normalize_scope_roots((str(primary.resolve(strict=False)), *(str(root) for root in roots)))


def _resolve_candidate(*, base_dir: Path, raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate.resolve(strict=False)


def _resolve_in_any_scope(*, path: Path, scope_roots: tuple[Path, ...], raw_path: str) -> Path:
    if not scope_roots:
        return path
    for scope_root in scope_roots:
        try:
            return resolve_in_scope(path, scope_root=scope_root, strict=False)
        except ValueError:
            continue
    raise WorkspacePathResolutionError(
        code="outside_scope",
        raw_path=raw_path,
        reason=f"Path outside scope: {raw_path}",
    )


def resolve_workspace_path(
    *,
    base_dir: Path,
    scope_roots: tuple[Path, ...] = (),
    raw_path: str,
    must_exist: bool,
) -> Path:
    """Resolve one path from base dir with optional hard scope enforcement."""

    resolved = _resolve_in_any_scope(
        path=_resolve_candidate(base_dir=base_dir, raw_path=raw_path),
        scope_roots=scope_roots,
        raw_path=raw_path,
    )
    if must_exist and not resolved.exists():
        raise WorkspacePathResolutionError(
            code="missing_path",
            raw_path=raw_path,
            reason=f"Path does not exist: {raw_path}",
        )
    return resolved


def resolve_io_path(
    *,
    base_dir: Path,
    scope_roots: tuple[Path, ...] = (),
    raw_path: str,
    must_exist: bool,
) -> Path:
    """Resolve file IO path from base dir with optional hard scope enforcement."""

    return resolve_workspace_path(
        base_dir=base_dir,
        scope_roots=scope_roots,
        raw_path=raw_path,
        must_exist=must_exist,
    )


def to_workspace_relative(*, base_dir: Path, path: Path) -> str:
    """Return stable base-dir-relative path string when possible."""

    try:
        return path.resolve(strict=False).relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return path.resolve(strict=False).as_posix()


def truncate_utf8(*, raw: bytes, max_bytes: int) -> tuple[str, bool]:
    """Decode bytes with truncation marker semantics."""

    if max_bytes < 1:
        max_bytes = 1
    if len(raw) <= max_bytes:
        return raw.decode("utf-8", errors="replace"), False
    return raw[:max_bytes].decode("utf-8", errors="replace"), True
