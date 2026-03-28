"""Helpers for fail-closed resolved path scope checks."""

from __future__ import annotations

from pathlib import Path


def resolve_in_scope(path: Path, *, scope_root: Path, strict: bool = False) -> Path:
    """Resolve path and ensure resolved location stays inside scope root."""

    scope = scope_root.resolve()
    resolved = path.resolve(strict=strict)
    if not resolved.is_relative_to(scope):
        raise ValueError(f"Path outside scope: {path}")
    return resolved


def resolve_in_scope_or_none(path: Path, *, scope_root: Path, strict: bool = True) -> Path | None:
    """Resolve and validate path scope; return None on any unsafe path state."""

    try:
        return resolve_in_scope(path, scope_root=scope_root, strict=strict)
    except (OSError, RuntimeError, ValueError):
        return None
