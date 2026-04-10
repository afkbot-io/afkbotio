"""Markdown loader for core and profile subagent descriptors."""

from __future__ import annotations

import re
import asyncio
from pathlib import Path
from typing import Literal

from afkbot.services.path_scope import resolve_in_scope_or_none
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.subagents.contracts import SubagentInfo
from afkbot.settings import Settings

_SUBAGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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

        resolved_name = name or self.DEFAULT_SUBAGENT
        self._validate_subagent_name(resolved_name)

        profile_path = self._safe_profile_subagent_path(profile_id, resolved_name)
        if await asyncio.to_thread(profile_path.exists):
            return SubagentInfo(name=resolved_name, path=profile_path, origin="profile")

        core_path = self._safe_core_subagent_path(resolved_name)
        if await asyncio.to_thread(core_path.exists):
            return SubagentInfo(name=resolved_name, path=core_path, origin="core")

        raise FileNotFoundError(f"Subagent not found: {resolved_name}")

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
        return await asyncio.to_thread(info.path.read_text, encoding="utf-8")

    def _discover_core_subagents(self) -> list[SubagentInfo]:
        root = self._settings.subagents_dir
        if not root.exists():
            return []
        return self._discover(root, origin="core")

    def _discover_profile_subagents(self, profile_id: str) -> list[SubagentInfo]:
        root = self._safe_profile_subagents_root(profile_id)
        if not root.exists():
            return []
        return self._discover(root, origin="profile")

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
