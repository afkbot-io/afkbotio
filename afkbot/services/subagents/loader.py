"""Markdown loader for core and profile subagent descriptors."""

from __future__ import annotations

import re
import asyncio
from pathlib import Path
from threading import Lock
from typing import Literal

from afkbot.services.naming import normalize_runtime_name
from afkbot.services.path_scope import resolve_in_scope_or_none
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.subagents.contracts import SubagentInfo
from afkbot.settings import Settings

_SUBAGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_NOT_FOUND_HINT_MAX_NAMES = 8
_DISCOVERY_CACHE_LOCK = Lock()
_SUBAGENT_DISCOVERY_CACHE: dict[
    tuple[str, str],
    tuple[tuple[tuple[str, tuple[int, int] | None], ...], tuple[SubagentInfo, ...]],
] = {}
_SUBAGENT_TEXT_CACHE: dict[str, tuple[tuple[int, int], str]] = {}


def reset_subagent_loader_caches() -> None:
    """Reset process-local subagent discovery caches (used by tests)."""

    with _DISCOVERY_CACHE_LOCK:
        _SUBAGENT_DISCOVERY_CACHE.clear()
        _SUBAGENT_TEXT_CACHE.clear()


class SubagentLoader:
    """Load subagent markdown descriptors from filesystem."""

    DEFAULT_SUBAGENT = "researcher"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def list_subagents(self, profile_id: str) -> list[SubagentInfo]:
        """List merged core + profile subagents with profile override precedence."""

        merged: dict[str, SubagentInfo] = {}
        core_items, profile_items = await asyncio.gather(
            asyncio.to_thread(self._discover_core_subagents),
            asyncio.to_thread(self._discover_profile_subagents, profile_id),
        )
        for item in core_items:
            merged[item.name] = item
        for item in profile_items:
            merged[item.name] = item
        return sorted(merged.values(), key=lambda item: item.name)

    async def resolve_subagent(self, name: str | None, profile_id: str) -> SubagentInfo:
        """Resolve one subagent descriptor by name with default fallback."""

        if name is None:
            resolved_name = self.DEFAULT_SUBAGENT
        else:
            try:
                resolved_name = normalize_runtime_name(name)
            except ValueError as exc:
                raise ValueError(f"Invalid subagent name: {name}") from exc
        self._validate_subagent_name(resolved_name)

        profile_path = self._safe_profile_subagent_path(profile_id, resolved_name)
        if await asyncio.to_thread(profile_path.exists):
            return SubagentInfo(name=resolved_name, path=profile_path, origin="profile")

        core_path = self._safe_core_subagent_path(resolved_name)
        if await asyncio.to_thread(core_path.exists):
            return SubagentInfo(name=resolved_name, path=core_path, origin="core")

        available = await self.list_subagents(profile_id)
        available_names = tuple(item.name for item in available)
        requested_label = _requested_subagent_label(raw_name=name, resolved_name=resolved_name)
        raise FileNotFoundError(
            _format_missing_subagent_error(
                requested_label=requested_label,
                available_names=available_names,
            )
        )

    def validate_subagent_name(self, name: str) -> None:
        """Validate subagent name."""

        self._validate_subagent_name(name)

    def profile_subagent_path(self, profile_id: str, name: str) -> Path:
        """Return safe profile path for one custom subagent markdown file."""

        self._validate_subagent_name(name)
        return self._safe_profile_subagent_path(profile_id, name)

    async def load_subagent_markdown(self, name: str | None, profile_id: str) -> str:
        """Load markdown content for a resolved subagent descriptor."""

        info = await self.resolve_subagent(name=name, profile_id=profile_id)
        return await asyncio.to_thread(self._read_cached_text, info.path)

    def _discover_core_subagents(self) -> list[SubagentInfo]:
        root = self._settings.subagents_dir
        return self._discover_cached(root=root, origin="core", cache_namespace="core")

    def _discover_profile_subagents(self, profile_id: str) -> list[SubagentInfo]:
        root = self._safe_profile_subagents_root(profile_id)
        return self._discover_cached(
            root=root,
            origin="profile",
            cache_namespace=f"profile:{profile_id}",
        )

    def _discover(self, root: Path, origin: Literal["core", "profile"]) -> list[SubagentInfo]:
        result: list[SubagentInfo] = []
        root_resolved = root.resolve()
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix != ".md":
                continue
            safe_path = resolve_in_scope_or_none(path, scope_root=root_resolved, strict=True)
            if safe_path is None:
                continue
            name = path.stem
            if not _SUBAGENT_NAME_RE.match(name):
                continue
            result.append(SubagentInfo(name=name, path=safe_path, origin=origin))
        return result

    def _discover_cached(
        self,
        *,
        root: Path,
        origin: Literal["core", "profile"],
        cache_namespace: str,
    ) -> list[SubagentInfo]:
        if not root.exists():
            return []
        cache_key = (cache_namespace, str(root.resolve()))
        signature = self._discover_signature(root)
        with _DISCOVERY_CACHE_LOCK:
            cached = _SUBAGENT_DISCOVERY_CACHE.get(cache_key)
            if cached is not None and cached[0] == signature:
                return list(cached[1])
        discovered = tuple(self._discover(root, origin=origin))
        with _DISCOVERY_CACHE_LOCK:
            _SUBAGENT_DISCOVERY_CACHE[cache_key] = (signature, discovered)
        return list(discovered)

    def _discover_signature(
        self,
        root: Path,
    ) -> tuple[tuple[str, tuple[int, int] | None], ...]:
        entries: list[tuple[str, tuple[int, int] | None]] = []
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.suffix != ".md":
                continue
            entries.append((path.name, self._path_signature(path)))
        return tuple(entries)

    @staticmethod
    def _validate_subagent_name(name: str) -> None:
        if not _SUBAGENT_NAME_RE.match(name):
            raise ValueError(f"Invalid subagent name: {name}")

    def _safe_core_subagent_path(self, name: str) -> Path:
        root = self._settings.subagents_dir.resolve()
        try:
            path = (root / f"{name}.md").resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Invalid subagent path: {name}") from exc
        if not path.is_relative_to(root):
            raise ValueError(f"Invalid subagent path: {name}")
        return path

    def _safe_profile_subagents_root(self, profile_id: str) -> Path:
        validate_profile_id(profile_id)
        profiles_root = self._settings.profiles_dir.resolve()
        root = (profiles_root / profile_id / "subagents").resolve()
        if not root.is_relative_to(profiles_root):
            raise ValueError(f"Invalid profile root: {profile_id}")
        return root

    def _safe_profile_subagent_path(self, profile_id: str, name: str) -> Path:
        root = self._safe_profile_subagents_root(profile_id)
        try:
            path = (root / f"{name}.md").resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Invalid profile subagent path: {name}") from exc
        if not path.is_relative_to(root):
            raise ValueError(f"Invalid profile subagent path: {name}")
        return path

    def _read_cached_text(self, path: Path) -> str:
        signature = self._path_signature(path)
        if signature is None:
            raise FileNotFoundError(path)
        cache_key = str(path.resolve())
        with _DISCOVERY_CACHE_LOCK:
            cached = _SUBAGENT_TEXT_CACHE.get(cache_key)
            if cached is not None and cached[0] == signature:
                return cached[1]
        text = path.read_text(encoding="utf-8")
        with _DISCOVERY_CACHE_LOCK:
            _SUBAGENT_TEXT_CACHE[cache_key] = (signature, text)
        return text

    @staticmethod
    def _path_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat_result = path.stat()
        except OSError:
            return None
        return (stat_result.st_mtime_ns, stat_result.st_size)


def _requested_subagent_label(*, raw_name: str | None, resolved_name: str) -> str:
    if raw_name is None:
        return resolved_name
    stripped = raw_name.strip()
    if not stripped:
        return resolved_name
    if stripped == resolved_name:
        return resolved_name
    return f"{stripped} (normalized: {resolved_name})"


def _format_missing_subagent_error(
    *,
    requested_label: str,
    available_names: tuple[str, ...],
) -> str:
    if not available_names:
        return f"Subagent not found: {requested_label}"
    visible_names = ", ".join(available_names[:_NOT_FOUND_HINT_MAX_NAMES])
    remaining = len(available_names) - min(len(available_names), _NOT_FOUND_HINT_MAX_NAMES)
    suffix = f", and {remaining} more" if remaining > 0 else ""
    return (
        f"Subagent not found: {requested_label}. "
        f"Available subagents: {visible_names}{suffix}"
    )
