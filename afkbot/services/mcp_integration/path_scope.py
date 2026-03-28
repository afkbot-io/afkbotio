"""Shared path-safety helpers for profile-scoped MCP filesystem access."""

from __future__ import annotations

from pathlib import Path

from afkbot.services.mcp_integration.errors import MCPStorageError
from afkbot.services.profile_id import validate_profile_id
from afkbot.settings import Settings


def safe_profile_root(settings: Settings, profile_id: str) -> Path:
    """Return one resolved profile root constrained to the configured profiles directory."""

    validate_profile_id(profile_id)
    profiles_root = settings.profiles_dir.resolve()
    profile_root = (profiles_root / profile_id).resolve()
    if not profile_root.is_relative_to(profiles_root):
        raise MCPStorageError(f"Invalid profile root: {profile_id}")
    return profile_root


def safe_profile_child(root: Path, *parts: str) -> Path:
    """Resolve one profile-local child path while rejecting scope escapes through symlinks."""

    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*parts).resolve(strict=False)
    if not candidate.is_relative_to(root_resolved):
        joined = "/".join(parts)
        raise MCPStorageError(f"Invalid config path outside profile scope: {joined}")
    return candidate


def ensure_path_in_scope(path: Path, *, scope_root: Path) -> Path:
    """Resolve one path and ensure it remains inside the provided scope root."""

    scope = scope_root.resolve()
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(scope):
        raise MCPStorageError(f"Invalid config path outside profile scope: {path}")
    return resolved
