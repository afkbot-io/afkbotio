"""Profile-scoped CRUD service for markdown skills."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from afkbot.services.naming import normalize_runtime_name
from afkbot.services.policy import get_profile_files_lock
from afkbot.services.skills.markdown import canonicalize_skill_markdown, extract_summary
from afkbot.services.skills.skills import SkillLoader
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "ProfileSkillService"] = {}


class ProfileSkillRecord(BaseModel):
    """Serializable profile skill descriptor returned by CRUD operations."""

    name: str = Field(min_length=1)
    origin: str = "profile"
    path: str = Field(min_length=1)
    content: str | None = None
    summary: str = ""
    available: bool = True
    missing_requirements: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    manifest_path: str | None = None
    manifest_valid: bool = True
    manifest_errors: tuple[str, ...] = ()
    execution_mode: str = "advisory"
    tool_names: tuple[str, ...] = ()
    app_names: tuple[str, ...] = ()
    preferred_tool_order: tuple[str, ...] = ()


class ProfileSkillNormalizeRecord(BaseModel):
    """Manifest normalization result for one profile-local skill."""

    name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    manifest_path: str = Field(min_length=1)
    action: Literal["created", "repaired", "overwritten", "skipped"]
    available: bool = True
    execution_mode: str = "advisory"
    missing_requirements: tuple[str, ...] = ()


class ProfileSkillService:
    """Manage custom profile skills stored as `<profile>/skills/<name>/SKILL.md`."""

    def __init__(self, settings: Settings, loader: SkillLoader | None = None) -> None:
        self._settings = settings
        self._loader = loader or SkillLoader(settings)
        self._profile_files_lock = get_profile_files_lock(root_dir=settings.root_dir)

    async def list(
        self,
        *,
        profile_id: str,
        scope: Literal["all", "profile", "core"] = "all",
        include_unavailable: bool = False,
    ) -> list[ProfileSkillRecord]:
        """List skills for one profile by scope."""

        if scope == "profile":
            skills = await self._loader.list_profile_skills(profile_id)
        elif scope == "core":
            skills = await self._loader.list_core_skills()
        else:
            skills = await self._loader.list_skills(profile_id)

        result: list[ProfileSkillRecord] = []
        for item in skills:
            if not include_unavailable and not item.available:
                continue
            result.append(
                ProfileSkillRecord(
                    name=item.name,
                    origin=item.origin,
                    path=self._to_relative(item.path),
                    summary=item.summary,
                    available=item.available,
                    missing_requirements=item.missing_requirements,
                    aliases=item.aliases,
                    manifest_path=(
                        None if item.manifest_path is None else self._to_relative(item.manifest_path)
                    ),
                    manifest_valid=item.manifest_valid,
                    manifest_errors=item.manifest_errors,
                    execution_mode=item.manifest.execution_mode,
                    tool_names=item.manifest.tool_names,
                    app_names=item.manifest.app_names,
                    preferred_tool_order=item.manifest.preferred_tool_order,
                )
            )
        return result

    async def get(
        self,
        *,
        profile_id: str,
        name: str,
        scope: Literal["all", "profile", "core"] = "all",
    ) -> ProfileSkillRecord:
        """Read one skill markdown by user-provided name and scope."""

        normalized_name = normalize_runtime_name(name)
        self._loader.validate_skill_name(normalized_name)

        if scope == "profile":
            path = self._loader.profile_skill_path(profile_id, normalized_name)
            if not path.exists():
                raise FileNotFoundError(f"Profile skill not found: {normalized_name}")
            content = path.read_text(encoding="utf-8")
            skill_info = self._loader._build_skill_info(name=normalized_name, path=path, origin="profile")  # noqa: SLF001
            return ProfileSkillRecord(
                name=normalized_name,
                origin="profile",
                path=self._to_relative(path),
                content=content,
                summary=_extract_summary(content),
                manifest_path=(
                    self._to_relative(self._loader.profile_skill_manifest_path(profile_id, normalized_name))
                    if self._loader.profile_skill_manifest_path(profile_id, normalized_name).exists()
                    else None
                ),
                manifest_valid=skill_info.manifest_valid,
                manifest_errors=skill_info.manifest_errors,
                available=skill_info.available,
                missing_requirements=skill_info.missing_requirements,
                aliases=skill_info.aliases,
                execution_mode=skill_info.manifest.execution_mode,
                tool_names=skill_info.manifest.tool_names,
                app_names=skill_info.manifest.app_names,
                preferred_tool_order=skill_info.manifest.preferred_tool_order,
            )

        listed = await self.list(
            profile_id=profile_id,
            scope=scope,
            include_unavailable=True,
        )
        selected = next((item for item in listed if item.name == normalized_name), None)
        if selected is None:
            scope_title = "Core" if scope == "core" else "Skill"
            raise FileNotFoundError(f"{scope_title} not found: {normalized_name}")
        if not selected.available:
            raise FileNotFoundError(f"Skill is unavailable: {normalized_name}")

        selected_path = Path(selected.path)
        root = self._settings.root_dir.resolve()
        if not selected_path.is_absolute():
            selected_path = root / selected_path
        try:
            resolved_path = selected_path.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise FileNotFoundError(f"Skill not found: {normalized_name}") from exc
        content_text = resolved_path.read_text(encoding="utf-8")
        return ProfileSkillRecord(
            name=selected.name,
            origin=selected.origin,
            path=selected.path,
            content=content_text,
            summary=_extract_summary(content_text),
            available=selected.available,
            missing_requirements=selected.missing_requirements,
            aliases=selected.aliases,
            manifest_path=selected.manifest_path,
            manifest_valid=selected.manifest_valid,
            manifest_errors=selected.manifest_errors,
            execution_mode=selected.execution_mode,
            tool_names=selected.tool_names,
            app_names=selected.app_names,
            preferred_tool_order=selected.preferred_tool_order,
        )

    async def upsert(self, *, profile_id: str, name: str, content: str) -> ProfileSkillRecord:
        """Create or replace one profile skill markdown file.

        Name is normalized to runtime-safe slug so users can provide localized labels.
        """

        normalized_name = normalize_runtime_name(name)
        self._loader.validate_skill_name(normalized_name)
        path = self._loader.profile_skill_path(profile_id, normalized_name)
        normalized_content = canonicalize_skill_markdown(name=normalized_name, content=content)
        async with self._profile_files_lock.acquire(profile_id):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(normalized_content, encoding="utf-8")
            materialized = self._loader.materialize_manifest(
                skill_path=path,
                name=normalized_name,
                content=normalized_content,
            )
            skill_info = self._loader._build_skill_info(name=normalized_name, path=path, origin="profile")  # noqa: SLF001
        return ProfileSkillRecord(
            name=normalized_name,
            path=self._to_relative(path),
            content=normalized_content,
            summary=extract_summary(normalized_content),
            manifest_path=(
                self._to_relative(materialized.path)
                if skill_info.manifest_path is None
                else self._to_relative(skill_info.manifest_path)
            ),
            manifest_valid=skill_info.manifest_valid,
            manifest_errors=skill_info.manifest_errors,
            execution_mode=skill_info.manifest.execution_mode,
            tool_names=skill_info.manifest.tool_names,
            app_names=skill_info.manifest.app_names,
            preferred_tool_order=skill_info.manifest.preferred_tool_order,
            available=skill_info.available,
            missing_requirements=skill_info.missing_requirements,
        )

    async def delete(self, *, profile_id: str, name: str) -> ProfileSkillRecord:
        """Delete one profile skill markdown file by user-provided name."""

        normalized_name = normalize_runtime_name(name)
        self._loader.validate_skill_name(normalized_name)
        path = self._loader.profile_skill_path(profile_id, normalized_name)
        async with self._profile_files_lock.acquire(profile_id):
            if not path.exists():
                raise FileNotFoundError(f"Profile skill not found: {normalized_name}")
            path.unlink()
            manifest_path = self._loader.profile_skill_manifest_path(profile_id, normalized_name)
            if manifest_path.exists():
                manifest_path.unlink()
            try:
                path.parent.rmdir()
            except OSError:
                # Parent can remain when additional files exist.
                pass
        return ProfileSkillRecord(name=normalized_name, path=self._to_relative(path))

    async def normalize_manifests(
        self,
        *,
        profile_id: str,
        name: str | None = None,
        overwrite: bool = False,
    ) -> builtins.list[ProfileSkillNormalizeRecord]:
        """Create or repair AFKBOT skill manifests for profile-local skills."""

        if name is not None:
            normalized_name = normalize_runtime_name(name)
            self._loader.validate_skill_name(normalized_name)
            names = [normalized_name]
        else:
            names = [item.name for item in await self._loader.list_profile_skills(profile_id)]

        results: builtins.list[ProfileSkillNormalizeRecord] = []
        async with self._profile_files_lock.acquire(profile_id):
            for normalized_name in names:
                path = self._loader.profile_skill_path(profile_id, normalized_name)
                if not path.exists():
                    raise FileNotFoundError(f"Profile skill not found: {normalized_name}")
                content = path.read_text(encoding="utf-8")
                materialized = self._loader.materialize_manifest(
                    skill_path=path,
                    name=normalized_name,
                    content=content,
                    overwrite=overwrite,
                )
                refreshed = self._loader._build_skill_info(name=normalized_name, path=path, origin="profile")  # noqa: SLF001
                results.append(
                    ProfileSkillNormalizeRecord(
                        name=normalized_name,
                        path=self._to_relative(path),
                        manifest_path=self._to_relative(materialized.path),
                        action=materialized.action,
                        available=refreshed.available,
                        execution_mode=refreshed.manifest.execution_mode,
                        missing_requirements=refreshed.missing_requirements,
                    )
                )
        return results

    def _to_relative(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            return str(path.resolve())


def get_profile_skill_service(settings: Settings) -> ProfileSkillService:
    """Return cached profile skill service bound to one root directory."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = ProfileSkillService(settings=settings)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_profile_skill_services() -> None:
    """Reset cached profile skill services for tests."""

    _SERVICES_BY_ROOT.clear()


def _extract_summary(content: str) -> str:
    """Extract deterministic one-line summary from markdown content."""

    return extract_summary(content)
