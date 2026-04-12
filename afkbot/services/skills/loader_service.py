"""Service implementation for filesystem-backed AFKBOT skills."""

from __future__ import annotations

import asyncio
from pathlib import Path

from afkbot.services.path_scope import resolve_in_scope_or_none
from afkbot.services.plugins import get_plugin_service
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.skills.loader_availability import check_skill_availability
from afkbot.services.skills.loader_contracts import (
    SkillInfo,
    SkillManifest,
    SkillManifestAction,
    SkillManifestMaterialization,
)
from afkbot.services.skills.loader_manifest import (
    SKILL_NAME_RE,
    build_default_manifest,
    build_manifest,
    load_overlay,
    manifest_path_for_skill,
    render_manifest_toml,
    safe_manifest_path,
    safe_skill_path,
    validate_skill_name,
)
from afkbot.services.skills.markdown import FrontmatterValue, parse_frontmatter
from afkbot.settings import Settings


class SkillLoader:
    """Load and list markdown skills from the core and profile-local filesystem."""

    ALWAYS_SKILLS: tuple[str, ...] = ("security-secrets",)

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def list_skills(self, profile_id: str) -> list[SkillInfo]:
        """List all visible skills, including mandatory always skills."""

        core, plugin, profile = await asyncio.gather(
            asyncio.to_thread(self._discover_core_skills),
            asyncio.to_thread(self._discover_plugin_skills),
            asyncio.to_thread(self._discover_profile_skills, profile_id),
        )
        core_map = {item.name: item for item in core}
        merged: dict[str, SkillInfo] = {item.name: item for item in [*core, *plugin, *profile]}

        for skill_name in self.ALWAYS_SKILLS:
            core_skill = core_map.get(skill_name)
            if core_skill is not None:
                merged[skill_name] = core_skill
                continue
            merged[skill_name] = self._build_mandatory_unavailable_skill(skill_name)

        return sorted(merged.values(), key=lambda item: item.name)

    async def list_profile_skills(self, profile_id: str) -> list[SkillInfo]:
        """List only profile-local skills without mandatory core rewrites."""

        return await asyncio.to_thread(self._discover_profile_skills, profile_id)

    async def list_core_skills(self) -> list[SkillInfo]:
        """List only core skills from the built-in skills directory."""

        return await asyncio.to_thread(self._discover_core_skills)

    async def load_skill(self, name: str, profile_id: str) -> str:
        """Load one skill markdown file with mandatory/core precedence rules."""

        self._validate_skill_name(name)
        profile_root = self._safe_profile_skills_root(profile_id)
        if name in self.ALWAYS_SKILLS:
            core_path = self._safe_skill_path(self._settings.skills_dir, name)
            if await asyncio.to_thread(core_path.exists):
                return await asyncio.to_thread(core_path.read_text, encoding="utf-8")
            raise FileNotFoundError(f"Skill not found: {name}")

        profile_path = self._safe_skill_path(profile_root, name)
        if await asyncio.to_thread(profile_path.exists):
            return await asyncio.to_thread(profile_path.read_text, encoding="utf-8")

        for plugin_root in self._plugin_skill_roots():
            plugin_path = self._safe_skill_path(plugin_root, name)
            if await asyncio.to_thread(plugin_path.exists):
                return await asyncio.to_thread(plugin_path.read_text, encoding="utf-8")

        core_path = self._safe_skill_path(self._settings.skills_dir, name)
        if await asyncio.to_thread(core_path.exists):
            return await asyncio.to_thread(core_path.read_text, encoding="utf-8")

        raise FileNotFoundError(f"Skill not found: {name}")

    def validate_skill_name(self, name: str) -> None:
        """Validate one user-provided skill name for profile CRUD operations."""

        self._validate_skill_name(name)

    def profile_skill_path(self, profile_id: str, name: str) -> Path:
        """Return the safe profile-local markdown path for one custom skill."""

        self._validate_skill_name(name)
        return self._safe_skill_path(self._safe_profile_skills_root(profile_id), name)

    def profile_skill_manifest_path(self, profile_id: str, name: str) -> Path:
        """Return the safe profile-local manifest path for one custom skill."""

        self._validate_skill_name(name)
        return self._safe_manifest_path(self._safe_profile_skills_root(profile_id), name)

    def materialize_manifest(
        self,
        *,
        skill_path: Path,
        name: str,
        content: str,
        source_kind: str = "",
        source_id: str = "",
        source_url: str = "",
        overwrite: bool = False,
    ) -> SkillManifestMaterialization:
        """Create, repair, or reuse one adjacent AFKBOT manifest from markdown."""

        metadata = parse_frontmatter(content)
        manifest_path = self._manifest_path_for_skill(skill_path)
        existed_before = manifest_path.exists()
        overlay: dict[str, object] = {}
        manifest_errors: tuple[str, ...] = ()
        if existed_before:
            overlay, manifest_errors = self._load_overlay(manifest_path)
        if existed_before and not overwrite and not manifest_errors:
            manifest = self._build_manifest(
                name=name,
                content=content,
                metadata=metadata,
                overlay=overlay,
            )
            return SkillManifestMaterialization(
                path=manifest_path,
                action="skipped",
                manifest=manifest,
            )

        manifest = self.build_default_manifest(
            name=name,
            content=content,
            source_kind=source_kind,
            source_id=source_id,
            source_url=source_url,
        )
        manifest_path.write_text(self.render_manifest_toml(manifest), encoding="utf-8")
        if not existed_before:
            action: SkillManifestAction = "created"
        elif overwrite:
            action = "overwritten"
        else:
            action = "repaired"
        return SkillManifestMaterialization(
            path=manifest_path,
            action=action,
            manifest=manifest,
        )

    def _discover_core_skills(self) -> list[SkillInfo]:
        root = self._settings.skills_dir
        if not root.exists():
            return []
        return self._discover(root, "core")

    def _discover_profile_skills(self, profile_id: str) -> list[SkillInfo]:
        root = self._safe_profile_skills_root(profile_id)
        if not root.exists():
            return []
        return self._discover(root, "profile")

    def _discover_plugin_skills(self) -> list[SkillInfo]:
        result: list[SkillInfo] = []
        for root in self._plugin_skill_roots():
            if not root.exists():
                continue
            result.extend(self._discover(root, f"plugin:{root.name}"))
        return result

    def _plugin_skill_roots(self) -> tuple[Path, ...]:
        return get_plugin_service(self._settings).skill_dirs()

    def _discover(self, root: Path, origin: str) -> list[SkillInfo]:
        result: list[SkillInfo] = []
        root_resolved = root.resolve()
        for skill_dir in sorted(root.iterdir(), key=lambda item: item.name):
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            if not SKILL_NAME_RE.match(skill_name):
                continue
            path = skill_dir / "SKILL.md"
            safe_path = resolve_in_scope_or_none(path, scope_root=root_resolved, strict=True)
            if safe_path is None:
                continue
            result.append(self._build_skill_info(name=skill_name, path=safe_path, origin=origin))
        return result

    def _mandatory_skill_placeholder_path(self, name: str) -> Path:
        """Build a deterministic in-scope placeholder path for unavailable mandatory skills."""

        return self._settings.skills_dir.resolve() / name / "SKILL.md"

    def _build_mandatory_unavailable_skill(self, name: str) -> SkillInfo:
        """Build fail-closed metadata for mandatory core skills that are unavailable."""

        try:
            path = self._safe_skill_path(self._settings.skills_dir, name)
            return self._build_skill_info(name=name, path=path, origin="core")
        except ValueError:
            return SkillInfo(
                name=name,
                path=self._mandatory_skill_placeholder_path(name),
                origin="core",
                available=False,
                missing_requirements=("unsafe_path",),
                missing_suggested_requirements=(),
                summary="",
                aliases=(),
                manifest=SkillManifest(name=name, description=""),
            )

    def _safe_profile_skills_root(self, profile_id: str) -> Path:
        """Return the safe profile-local skills root and reject path traversal."""

        validate_profile_id(profile_id)
        profiles_root = self._settings.profiles_dir.resolve()
        candidate = (profiles_root / profile_id / "skills").resolve()
        if not candidate.is_relative_to(profiles_root):
            raise ValueError(f"Invalid profile root: {profile_id}")
        return candidate

    def _build_skill_info(self, name: str, path: Path, origin: str) -> SkillInfo:
        """Build one skill descriptor with manifest and availability metadata."""

        content = ""
        metadata: dict[str, FrontmatterValue] = {}
        manifest_path = self._manifest_path_for_skill(path)
        overlay: dict[str, object] = {}
        manifest_errors: tuple[str, ...] = ()
        if path.exists():
            content = path.read_text(encoding="utf-8")
            metadata = parse_frontmatter(content)
        if manifest_path.exists():
            overlay, manifest_errors = self._load_overlay(manifest_path)
        manifest = self._build_manifest(
            name=name,
            content=content,
            metadata=metadata,
            overlay=overlay,
        )
        available, missing, suggested_missing = check_skill_availability(
            path=path,
            settings=self._settings,
            metadata=metadata,
            manifest=manifest,
            manifest_errors=manifest_errors,
        )
        return SkillInfo(
            name=name,
            path=path,
            origin=origin,
            available=available,
            missing_requirements=tuple(sorted(missing)),
            missing_suggested_requirements=tuple(sorted(suggested_missing)),
            summary=manifest.description,
            aliases=manifest.aliases,
            manifest=manifest,
            manifest_path=manifest_path if manifest_path.exists() else None,
            manifest_valid=not manifest_errors,
            manifest_errors=manifest_errors,
        )

    @staticmethod
    def _validate_skill_name(name: str) -> None:
        """Compatibility wrapper for skill-name validation."""

        validate_skill_name(name)

    @staticmethod
    def _safe_skill_path(root: Path, name: str) -> Path:
        """Compatibility wrapper for safe markdown path resolution."""

        return safe_skill_path(root, name)

    @staticmethod
    def _manifest_path_for_skill(path: Path) -> Path:
        """Compatibility wrapper for manifest path resolution."""

        return manifest_path_for_skill(path)

    @staticmethod
    def _safe_manifest_path(root: Path, name: str) -> Path:
        """Compatibility wrapper for safe manifest path resolution."""

        return safe_manifest_path(root, name)

    @staticmethod
    def _load_overlay(path: Path) -> tuple[dict[str, object], tuple[str, ...]]:
        """Compatibility wrapper for manifest overlay loading."""

        return load_overlay(path)

    @staticmethod
    def _build_manifest(
        *,
        name: str,
        content: str,
        metadata: dict[str, FrontmatterValue],
        overlay: dict[str, object] | None = None,
    ) -> SkillManifest:
        """Compatibility wrapper for manifest building."""

        return build_manifest(name=name, content=content, metadata=metadata, overlay=overlay)

    @staticmethod
    def build_default_manifest(
        *,
        name: str,
        content: str,
        source_kind: str = "",
        source_id: str = "",
        source_url: str = "",
    ) -> SkillManifest:
        """Compatibility wrapper for default manifest construction."""

        return build_default_manifest(
            name=name,
            content=content,
            source_kind=source_kind,
            source_id=source_id,
            source_url=source_url,
        )

    @staticmethod
    def render_manifest_toml(manifest: SkillManifest) -> str:
        """Compatibility wrapper for TOML manifest rendering."""

        return render_manifest_toml(manifest)
