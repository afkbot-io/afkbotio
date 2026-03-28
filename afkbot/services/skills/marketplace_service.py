"""Marketplace orchestration for listing and installing profile-local skills."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Callable

from afkbot.services.policy import get_profile_files_lock
from afkbot.services.profile_id import validate_profile_id
from afkbot.services.skills.marketplace_contracts import (
    SkillMarketplaceError,
    SkillMarketplaceInstallRecord,
    SkillMarketplaceListItem,
    SkillMarketplaceListResult,
    SourceDescriptor,
)
from afkbot.services.skills.marketplace_fetch import (
    MarketplaceContentFetcher,
    extract_heading,
    extract_marketplace_summary,
)
from afkbot.services.skills.marketplace_popularity import MarketplacePopularityResolver
from afkbot.services.skills.marketplace_sources import MarketplaceSourceResolver
from afkbot.services.skills.skills import SkillInfo, SkillLoader
from afkbot.settings import Settings

_SERVICES_BY_ROOT: dict[str, "SkillMarketplaceService"] = {}
_DEFAULT_SOURCE_ALIASES = frozenset({"default", "recommended", "curated"})


class SkillMarketplaceService:
    """Install and list skills from skills.sh/GitHub compatible sources."""

    def __init__(
        self,
        settings: Settings,
        *,
        loader: SkillLoader | None = None,
        fetch_text: Callable[[str, int], str] | None = None,
    ) -> None:
        self._settings = settings
        self._loader = loader or SkillLoader(settings)
        self._profile_files_lock = get_profile_files_lock(root_dir=settings.root_dir)
        self._source_resolver = MarketplaceSourceResolver(
            skills_sh_hosts=frozenset(
                item.strip().lower()
                for item in settings.skills_marketplace_skills_sh_hosts
                if item.strip()
            ),
            github_hosts=frozenset(
                item.strip().lower()
                for item in settings.skills_marketplace_github_hosts
                if item.strip()
            ),
            raw_github_hosts=frozenset(
                item.strip().lower()
                for item in settings.skills_marketplace_raw_github_hosts
                if item.strip()
            ),
            default_ref_candidates=tuple(
                item.strip() for item in settings.skills_marketplace_default_refs if item.strip()
            )
            or ("main", "master"),
            default_skill_base_paths=tuple(settings.skills_marketplace_default_base_paths),
        )
        self._fetcher = MarketplaceContentFetcher(
            fetch_text=fetch_text,
            max_markdown_bytes=int(settings.skills_marketplace_max_markdown_bytes),
            max_json_bytes=int(settings.skills_marketplace_max_json_bytes),
            http_timeout_sec=int(settings.skills_marketplace_timeout_sec),
            http_user_agent=(
                str(settings.skills_marketplace_user_agent).strip() or "afkbot/skills-marketplace"
            ),
        )
        self._popularity = MarketplacePopularityResolver(
            fetcher=self._fetcher,
            default_skills_sh_host=(
                next(
                    (
                        item.strip()
                        for item in settings.skills_marketplace_skills_sh_hosts
                        if item.strip()
                    ),
                    "skills.sh",
                )
            ),
        )

    async def list_source(
        self,
        *,
        source: str,
        limit: int | None = None,
        profile_id: str | None = None,
    ) -> SkillMarketplaceListResult:
        """List available skill names from source URL/spec."""

        descriptor = self._parse_source(source)
        if descriptor.is_direct:
            items = await asyncio.to_thread(self._list_direct_source_sync, descriptor)
        else:
            items = await asyncio.to_thread(self._list_repo_source_sync, descriptor)
        items, source_stats = await asyncio.to_thread(
            self._popularity.enrich_listing,
            descriptor=descriptor,
            items=items,
        )
        items = await self._annotate_installed_items(items=items, profile_id=profile_id)
        return SkillMarketplaceListResult(
            source=descriptor.raw_source,
            items=tuple(_apply_item_limit(items, limit=limit)),
            source_stats=source_stats,
        )

    async def search_source(
        self,
        *,
        source: str,
        query: str,
        limit: int | None = None,
        profile_id: str | None = None,
    ) -> SkillMarketplaceListResult:
        """Search available marketplace skills by free-text query."""

        normalized_query = " ".join(query.split()).strip().lower()
        listing = await self.list_source(
            source=source,
            limit=None,
            profile_id=profile_id,
        )
        items = list(listing.items)
        if not normalized_query:
            return SkillMarketplaceListResult(
                source=listing.source,
                items=tuple(_apply_item_limit(items, limit=limit)),
                source_stats=listing.source_stats,
            )
        query_parts = tuple(part for part in normalized_query.split() if part)
        result: list[SkillMarketplaceListItem] = []
        for item in items:
            haystack = " ".join(
                (
                    item.name,
                    item.summary,
                    item.path,
                    item.source,
                    item.installed_name or "",
                )
            ).lower()
            if all(part in haystack for part in query_parts):
                result.append(item)
        return SkillMarketplaceListResult(
            source=listing.source,
            items=tuple(_apply_item_limit(result, limit=limit)),
            source_stats=listing.source_stats,
        )

    async def install(
        self,
        *,
        profile_id: str,
        source: str,
        skill: str | None = None,
        target_name: str | None = None,
        overwrite: bool = False,
    ) -> SkillMarketplaceInstallRecord:
        """Install one source skill to profile-local SKILL.md path."""

        validate_profile_id(profile_id)
        descriptor = self._parse_source(source)
        markdown, resolved_source, inferred_name = await asyncio.to_thread(
            self._resolve_markdown_sync,
            descriptor,
            skill,
        )
        install_name = self._resolve_install_name(
            target_name=target_name,
            requested_name=skill,
            inferred_name=inferred_name,
            markdown=markdown,
        )

        try:
            self._loader.validate_skill_name(install_name)
            path = self._loader.profile_skill_path(profile_id, install_name)
        except ValueError as exc:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_name",
                reason=str(exc),
            ) from exc

        async with self._profile_files_lock.acquire(profile_id):
            if path.exists() and not overwrite:
                raise SkillMarketplaceError(
                    error_code="skill_marketplace_already_exists",
                    reason=f"Skill already exists: {install_name}",
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
            materialized = self._loader.materialize_manifest(
                skill_path=path,
                name=install_name,
                content=markdown,
                source_kind="marketplace",
                source_id=resolved_source,
                source_url=resolved_source,
            )
            skill_info = self._loader._build_skill_info(name=install_name, path=path, origin="profile")  # noqa: SLF001

        return SkillMarketplaceInstallRecord(
            name=install_name,
            path=self._to_relative(path),
            source=resolved_source,
            summary=extract_marketplace_summary(markdown),
            manifest_path=(
                self._to_relative(materialized.path)
                if skill_info.manifest_path is None
                else self._to_relative(skill_info.manifest_path)
            ),
            available=skill_info.available,
            missing_requirements=skill_info.missing_requirements,
            execution_mode=skill_info.manifest.execution_mode,
        )

    def _list_direct_source_sync(self, descriptor: SourceDescriptor) -> list[SkillMarketplaceListItem]:
        if descriptor.direct_url is None:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source URL is not resolved",
            )
        markdown = self._fetcher.fetch_markdown(descriptor.direct_url)
        name = self._resolve_install_name(
            target_name=None,
            requested_name=None,
            inferred_name=descriptor.skill_hint,
            markdown=markdown,
        )
        return [
            SkillMarketplaceListItem(
                name=name,
                source=descriptor.raw_source,
                path=descriptor.direct_url,
                summary=extract_marketplace_summary(markdown),
                canonical_source=descriptor.direct_url,
            )
        ]

    def _list_repo_source_sync(self, descriptor: SourceDescriptor) -> list[SkillMarketplaceListItem]:
        owner = descriptor.owner
        repo = descriptor.repo
        if owner is None or repo is None:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source repository is not resolved",
            )

        for ref in self._source_resolver.candidate_refs(descriptor.ref):
            tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{ref}?recursive=1"
            try:
                payload = self._fetcher.fetch_json(tree_url)
            except SkillMarketplaceError as exc:
                if exc.error_code == "skill_marketplace_source_not_found":
                    continue
                raise

            names_with_paths = self._source_resolver.extract_skill_names_from_tree(
                payload=payload,
                base_path=descriptor.base_path,
            )
            if names_with_paths:
                return [
                    SkillMarketplaceListItem(
                        name=name,
                        source=descriptor.raw_source,
                        path=f"{owner}/{repo}/{ref}/{path}",
                        summary="",
                        canonical_source=self._source_resolver.build_raw_github_url(
                            owner,
                            repo,
                            ref,
                            path,
                        ),
                    )
                    for name, path in names_with_paths
                ]

        raise SkillMarketplaceError(
            error_code="skill_marketplace_source_not_found",
            reason="No skills found in source",
        )

    def _resolve_markdown_sync(
        self,
        descriptor: SourceDescriptor,
        requested_skill: str | None,
    ) -> tuple[str, str, str | None]:
        if descriptor.is_direct:
            if descriptor.direct_url is None:
                raise SkillMarketplaceError(
                    error_code="skill_marketplace_invalid_source",
                    reason="Source URL is not resolved",
                )
            markdown = self._fetcher.fetch_markdown(descriptor.direct_url)
            return markdown, descriptor.direct_url, descriptor.skill_hint

        owner = descriptor.owner
        repo = descriptor.repo
        if owner is None or repo is None:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_invalid_source",
                reason="Source repository is not resolved",
            )

        normalized_skill = self._source_resolver.normalize_optional_skill_name(
            requested_skill or descriptor.skill_hint
        )
        if normalized_skill is None:
            raise SkillMarketplaceError(
                error_code="skill_marketplace_skill_required",
                reason="Skill name is required for repository source",
            )

        for candidate in self._source_resolver.candidate_skill_urls(
            owner=owner,
            repo=repo,
            requested_skill=normalized_skill,
            ref=descriptor.ref,
            base_path=descriptor.base_path,
        ):
            try:
                markdown = self._fetcher.fetch_markdown(candidate)
                return markdown, candidate, normalized_skill
            except SkillMarketplaceError as exc:
                if exc.error_code == "skill_marketplace_source_not_found":
                    continue
                raise

        raise SkillMarketplaceError(
            error_code="skill_marketplace_skill_not_found",
            reason=f"Skill not found in source: {normalized_skill}",
        )

    def _resolve_install_name(
        self,
        *,
        target_name: str | None,
        requested_name: str | None,
        inferred_name: str | None,
        markdown: str,
    ) -> str:
        for candidate in (
            target_name,
            requested_name,
            inferred_name,
            extract_heading(markdown),
        ):
            normalized = self._source_resolver.normalize_optional_skill_name(candidate)
            if normalized is not None:
                return normalized
        raise SkillMarketplaceError(
            error_code="skill_marketplace_invalid_name",
            reason="Unable to infer a valid skill name",
        )

    def _parse_source(self, raw_source: str) -> SourceDescriptor:
        """Delegate source parsing to the dedicated resolver."""

        normalized_source = self._normalize_source(raw_source)
        return self._source_resolver.parse_source(normalized_source)

    def _normalize_source(self, raw_source: str) -> str:
        """Resolve built-in marketplace aliases to one canonical source."""

        source = raw_source.strip()
        if source.lower() in _DEFAULT_SOURCE_ALIASES:
            return self._settings.skills_marketplace_default_source
        return source

    def _to_relative(self, path: Path) -> str:
        root = self._settings.root_dir.resolve()
        try:
            return str(path.resolve().relative_to(root))
        except ValueError:
            return str(path.resolve())

    async def _annotate_installed_items(
        self,
        *,
        items: list[SkillMarketplaceListItem],
        profile_id: str | None,
    ) -> list[SkillMarketplaceListItem]:
        if profile_id is None:
            return items
        validate_profile_id(profile_id)
        installed_index = await self._build_installed_index(profile_id)
        if not installed_index.by_name and not installed_index.by_source:
            return items

        result: list[SkillMarketplaceListItem] = []
        for item in items:
            installed_record = (
                installed_index.by_source.get(item.canonical_source)
                if item.canonical_source
                else None
            )
            if installed_record is None:
                installed_record = installed_index.by_name.get(item.name)
            if installed_record is None:
                result.append(item)
                continue
            result.append(
                replace(
                    item,
                    installed=True,
                    installed_name=installed_record.name,
                    installed_origin=installed_record.origin,
                )
            )
        return result

    async def _build_installed_index(self, profile_id: str) -> "_InstalledMarketplaceIndex":
        skills = await self._loader.list_skills(profile_id)
        by_name: dict[str, _InstalledMarketplaceRecord] = {}
        by_source: dict[str, _InstalledMarketplaceRecord] = {}
        for skill in skills:
            record = _InstalledMarketplaceRecord(
                name=skill.name,
                origin=skill.origin,
            )
            if _should_match_marketplace_item_by_name(skill):
                by_name.setdefault(skill.name, record)
            source_keys = tuple(
                key.strip()
                for key in (
                    skill.manifest.source_id,
                    skill.manifest.source_url,
                )
                if key.strip()
            )
            for source_key in source_keys:
                by_source.setdefault(source_key, record)
        return _InstalledMarketplaceIndex(
            by_name=by_name,
            by_source=by_source,
        )


def get_skill_marketplace_service(settings: Settings) -> SkillMarketplaceService:
    """Return cached marketplace service for one workspace root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        service = SkillMarketplaceService(settings=settings)
    _SERVICES_BY_ROOT[key] = service
    return service


def _apply_item_limit(
    items: list[SkillMarketplaceListItem],
    *,
    limit: int | None,
) -> list[SkillMarketplaceListItem]:
    """Apply optional positive item limit without mutating the source list."""

    if limit is None:
        return list(items)
    normalized = max(1, int(limit))
    return list(items[:normalized])


def reset_skill_marketplace_services() -> None:
    """Reset marketplace service cache for tests."""

    _SERVICES_BY_ROOT.clear()


class _InstalledMarketplaceRecord:
    """Installed skill reference used for marketplace list annotations."""

    def __init__(self, *, name: str, origin: str) -> None:
        self.name = name
        self.origin = origin


class _InstalledMarketplaceIndex:
    """Lookup tables for installed marketplace items in one profile/runtime."""

    def __init__(
        self,
        *,
        by_name: dict[str, _InstalledMarketplaceRecord],
        by_source: dict[str, _InstalledMarketplaceRecord],
    ) -> None:
        self.by_name = by_name
        self.by_source = by_source


def _should_match_marketplace_item_by_name(skill: SkillInfo) -> bool:
    """Return whether a visible skill is safe to treat as an installed-name match."""

    if skill.origin == "core":
        return True
    return skill.manifest.source_kind == "marketplace"
