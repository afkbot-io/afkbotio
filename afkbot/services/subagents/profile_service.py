"""Profile-scoped CRUD service for markdown subagent descriptors."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from afkbot.services.naming import normalize_runtime_name
from afkbot.services.policy import get_profile_files_lock
from afkbot.services.subagents.loader import SubagentLoader
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "ProfileSubagentService"] = {}


class ProfileSubagentRecord(BaseModel):
    """Serializable profile subagent descriptor returned by CRUD operations."""

    name: str = Field(min_length=1)
    origin: str = "profile"
    path: str = Field(min_length=1)
    content: str | None = None
    summary: str = ""


class ProfileSubagentService:
    """Manage custom profile subagents stored as `<profile>/subagents/<name>.md`."""

    def __init__(self, settings: Settings, loader: SubagentLoader | None = None) -> None:
        self._settings = settings
        self._loader = loader or SubagentLoader(settings)
        self._profile_files_lock = get_profile_files_lock(root_dir=settings.root_dir)

    async def list(self, *, profile_id: str) -> list[ProfileSubagentRecord]:
        """List only profile-origin subagents for one profile."""

        subagents = await self._loader.list_subagents(profile_id)
        result: list[ProfileSubagentRecord] = []
        for item in subagents:
            if item.origin != "profile":
                continue
            content = item.path.read_text(encoding="utf-8")
            result.append(
                ProfileSubagentRecord(
                    name=item.name,
                    path=self._to_relative(item.path),
                    summary=_extract_summary(content),
                )
            )
        return result

    async def get(self, *, profile_id: str, name: str) -> ProfileSubagentRecord:
        """Read one profile subagent markdown by user-provided name."""

        normalized_name = normalize_runtime_name(name)
        self._loader.validate_subagent_name(normalized_name)
        path = self._loader.profile_subagent_path(profile_id, normalized_name)
        if not path.exists():
            raise FileNotFoundError(f"Profile subagent not found: {normalized_name}")
        content = path.read_text(encoding="utf-8")
        return ProfileSubagentRecord(
            name=normalized_name,
            path=self._to_relative(path),
            content=content,
            summary=_extract_summary(content),
        )

    async def upsert(self, *, profile_id: str, name: str, content: str) -> ProfileSubagentRecord:
        """Create or replace one profile subagent markdown file.

        Name is normalized to runtime-safe slug so users can provide localized labels.
        """

        normalized_name = normalize_runtime_name(name)
        self._loader.validate_subagent_name(normalized_name)
        path = self._loader.profile_subagent_path(profile_id, normalized_name)
        async with self._profile_files_lock.acquire(profile_id):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return ProfileSubagentRecord(
            name=normalized_name,
            path=self._to_relative(path),
            content=content,
            summary=_extract_summary(content),
        )

    async def delete(self, *, profile_id: str, name: str) -> ProfileSubagentRecord:
        """Delete one profile subagent markdown file by user-provided name."""

        normalized_name = normalize_runtime_name(name)
        self._loader.validate_subagent_name(normalized_name)
        path = self._loader.profile_subagent_path(profile_id, normalized_name)
        async with self._profile_files_lock.acquire(profile_id):
            if not path.exists():
                raise FileNotFoundError(f"Profile subagent not found: {normalized_name}")
            path.unlink()
        return ProfileSubagentRecord(name=normalized_name, path=self._to_relative(path))

    def _to_relative(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            return str(path.resolve())


def get_profile_subagent_service(settings: Settings) -> ProfileSubagentService:
    """Return cached profile subagent service bound to one root directory."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ProfileSubagentService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_profile_subagent_services() -> None:
    """Reset cached profile subagent services for tests."""

    _SERVICES_BY_ROOT.clear()


def _extract_summary(content: str) -> str:
    """Extract deterministic one-line summary from markdown content."""

    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("---"):
            continue
        if line.startswith("#"):
            text = line.lstrip("#").strip()
            if text:
                return " ".join(text.split())[:160]
            continue
        return " ".join(line.split())[:160]
    return ""
