"""Helpers for channel media downloads and model-facing attachment summaries."""

from __future__ import annotations

import re
from pathlib import Path

from afkbot.services.tools.text_snapshots import snapshot_path_text
from afkbot.services.tools.workspace import (
    WorkspacePathResolutionError,
    resolve_tool_workspace_base_dir,
    resolve_tool_workspace_scope_roots,
    resolve_workspace_path,
    to_workspace_relative,
)
from afkbot.settings import Settings

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/javascript",
}
_TEXT_SUFFIXES = {
    ".csv",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".rst",
    ".text",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}


def build_channel_attachment_dir(
    *,
    settings: Settings,
    profile_id: str,
    transport: str,
    endpoint_id: str,
    event_id: str,
) -> Path:
    """Return an in-scope workspace directory for downloaded channel attachments."""

    base_dir = resolve_tool_workspace_base_dir(settings=settings, profile_id=profile_id)
    path = base_dir / "channel_attachments" / safe_filename(transport) / safe_filename(endpoint_id) / safe_filename(event_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(value: str, *, fallback: str = "attachment") -> str:
    """Return a path-segment-safe filename without changing the filesystem scope."""

    normalized = Path(value.strip()).name
    normalized = _SAFE_FILENAME_RE.sub("_", normalized).strip("._-")
    return normalized or fallback


def relative_to_profile_workspace(*, settings: Settings, profile_id: str, path: Path) -> str:
    """Return the stable model-facing path relative to the active profile workspace."""

    base_dir = resolve_tool_workspace_base_dir(settings=settings, profile_id=profile_id)
    return to_workspace_relative(base_dir=base_dir, path=path)


def build_text_preview(
    *,
    path: Path,
    mime_type: str | None,
    max_bytes: int,
) -> tuple[str, bool] | None:
    """Return a bounded text preview for small text-like files."""

    if max_bytes < 1 or not _looks_text_like(path=path, mime_type=mime_type):
        return None
    try:
        content, truncated, _ = snapshot_path_text(path=path, max_bytes=max_bytes)
    except OSError:
        return None
    preview = content.strip()
    if not preview:
        return None
    return preview, truncated


async def resolve_channel_outbound_media_path(
    *,
    settings: Settings,
    profile_id: str,
    raw_value: str,
    label: str,
) -> Path | None:
    """Resolve local outbound media paths while preserving remote/file-id references."""

    normalized = raw_value.strip()
    if not normalized:
        raise ValueError(f"{label} value is empty")
    if normalized.startswith(("http://", "https://")):
        return None
    if not _looks_like_local_media_path(normalized):
        return None
    base_dir = resolve_tool_workspace_base_dir(settings=settings, profile_id=profile_id)
    scope_roots = await resolve_tool_workspace_scope_roots(settings=settings, profile_id=profile_id)
    try:
        path = resolve_workspace_path(
            base_dir=base_dir,
            scope_roots=scope_roots,
            raw_path=normalized,
            must_exist=True,
        )
    except WorkspacePathResolutionError as exc:
        if exc.code == "outside_scope":
            raise ValueError(f"{label} path is outside allowed workspace scope: {normalized}") from None
        if exc.code == "missing_path":
            raise ValueError(f"{label} path does not exist: {normalized}") from None
        raise ValueError(exc.reason) from None
    if not path.is_file():
        raise ValueError(f"{label} path is not a file: {normalized}")
    return path


def _looks_like_local_media_path(raw_value: str) -> bool:
    normalized = raw_value.strip()
    if not normalized:
        return False
    if normalized.startswith((".", "~", "/")) or "\\" in normalized:
        return True
    basename = Path(normalized).name
    if _looks_like_file_name(basename):
        return True
    if "/" in normalized:
        parts = tuple(part for part in normalized.split("/") if part)
        if any(part in {".", "..", "~"} for part in parts):
            return True
        if _looks_like_file_name(basename):
            return True
    return False


def _looks_like_file_name(raw_value: str) -> bool:
    normalized = raw_value.strip()
    if not normalized or normalized.startswith("."):
        return False
    if "." not in normalized:
        return False
    suffix = Path(normalized).suffix
    return bool(suffix and suffix != ".")


def _looks_text_like(*, path: Path, mime_type: str | None) -> bool:
    normalized = (mime_type or "").strip().lower()
    if normalized.startswith(_TEXT_MIME_PREFIXES) or normalized in _TEXT_MIME_TYPES:
        return True
    return path.suffix.lower() in _TEXT_SUFFIXES
