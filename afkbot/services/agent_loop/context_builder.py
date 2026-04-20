"""Context assembly from bootstrap files and skill metadata."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

from afkbot.services.profile_runtime.runtime_config import get_profile_runtime_config_service
from afkbot.services.skills.loader_service import reset_skill_loader_caches
from afkbot.services.skills.skills import SkillInfo, SkillLoader
from afkbot.services.subagents.contracts import SubagentInfo
from afkbot.services.subagents.loader import (
    SubagentLoader,
    reset_subagent_loader_caches,
)
from afkbot.settings import Settings

_CONTEXT_CACHE_LOCK = Lock()
_FORMATTED_FILE_CACHE: dict[tuple[str, str], tuple[tuple[int, int], str]] = {}
_SUBAGENT_SUMMARY_CACHE: dict[str, tuple[tuple[int, int], str]] = {}
_SUBAGENT_SUMMARY_MAX_CHARS = 160


def reset_context_builder_caches() -> None:
    """Reset process-local context builder caches (used by tests)."""

    with _CONTEXT_CACHE_LOCK:
        _FORMATTED_FILE_CACHE.clear()
        _SUBAGENT_SUMMARY_CACHE.clear()


def reset_context_asset_caches() -> None:
    """Reset local context builder and loader caches together."""

    reset_context_builder_caches()
    reset_skill_loader_caches()
    reset_subagent_loader_caches()


@dataclass(frozen=True, slots=True)
class ContextAssets:
    """Preloaded profile context assets for one turn."""

    skills: tuple[SkillInfo, ...]
    subagents: tuple[SubagentInfo, ...]
    skill_triggers: dict[str, str]
    explicit_skill_triggers: dict[str, str]
    subagent_names: set[str]


class ContextBuilder:
    """Build text context for a turn."""

    def __init__(
        self,
        settings: Settings,
        skills: SkillLoader,
        subagents: SubagentLoader | None = None,
    ) -> None:
        self._settings = settings
        self._skills = skills
        self._subagents = subagents or SubagentLoader(settings)

    @property
    def root_dir(self) -> Path:
        """Return project root directory used by context assets."""

        return self._settings.root_dir

    @property
    def settings(self) -> Settings:
        """Return settings backing bootstrap, skills, and profile assets."""

        return self._settings

    async def build(
        self,
        profile_id: str,
        runtime_metadata: dict[str, Any] | None = None,
        prompt_overlay: str | None = None,
        trusted_runtime_notes: str | None = None,
        relevant_skill_names: set[str] | None = None,
        relevant_subagent_names: set[str] | None = None,
        explicit_skill_names: set[str] | None = None,
        explicit_subagent_names: set[str] | None = None,
        assets: ContextAssets | None = None,
    ) -> str:
        """Build context from BOOTSTRAP_FILES and available skills summary."""

        resolved_assets = assets or await self.collect_assets(profile_id=profile_id)
        skills = list(resolved_assets.skills)
        subagents = list(resolved_assets.subagents)
        bootstrap, subagents_block = await asyncio.gather(
            self._read_bootstrap_files(profile_id),
            self._subagents_summary_block(
                subagents=subagents,
                relevant_subagent_names=relevant_subagent_names,
            ),
        )
        skills_block = self._skills_summary_block(
            skills=skills,
            relevant_skill_names=relevant_skill_names,
        )
        selected_skill_cards_block = self._selected_skill_cards_block(
            skills=skills,
            selected_skill_names=relevant_skill_names,
        )
        explicit_skills_block = await self._explicit_skills_block(
            profile_id=profile_id,
            explicit_skill_names=explicit_skill_names,
        )
        explicit_subagents_block = await self._explicit_subagents_block(
            profile_id=profile_id,
            explicit_subagent_names=explicit_subagent_names,
        )
        prompt_overlay_block = self._prompt_overlay_block(prompt_overlay)
        trusted_runtime_block = self._trusted_runtime_notes_block(trusted_runtime_notes)
        runtime = self._runtime_metadata_block(runtime_metadata)
        return "\n\n".join(
            [
                "# Bootstrap",
                bootstrap,
                "# Binding Prompt Overlay",
                prompt_overlay_block,
                "# Trusted Runtime Notes",
                trusted_runtime_block,
                "# Skills",
                skills_block,
                "# Selected Skill Cards",
                selected_skill_cards_block,
                "# Subagents",
                subagents_block,
                "# Explicit Skill Instructions",
                explicit_skills_block,
                "# Explicit Subagent Instructions",
                explicit_subagents_block,
                runtime,
            ]
        )

    async def load_skill(self, *, name: str, profile_id: str) -> str:
        """Load one skill markdown body by name for on-demand routing."""

        return await self._skills.load_skill(name=name, profile_id=profile_id)

    async def collect_assets(self, *, profile_id: str) -> ContextAssets:
        """Collect skills/subagents once to avoid repeated filesystem scans in one turn."""

        skills_list, subagents_list = await asyncio.gather(
            self._skills.list_skills(profile_id),
            self._subagents.list_subagents(profile_id),
        )
        skills = tuple(skills_list)
        subagents = tuple(subagents_list)
        skill_triggers: dict[str, str] = {}
        explicit_skill_triggers: dict[str, str] = {}
        for item in skills:
            canonical = item.name
            explicit_skill_triggers[canonical.lower()] = canonical
            if item.available:
                skill_triggers[canonical.lower()] = canonical
            for alias in item.aliases:
                lowered = alias.strip().lower()
                if not lowered:
                    continue
                explicit_skill_triggers.setdefault(lowered, canonical)
                if item.available:
                    skill_triggers.setdefault(lowered, canonical)
        subagent_names = {item.name for item in subagents}
        return ContextAssets(
            skills=skills,
            subagents=subagents,
            skill_triggers=skill_triggers,
            explicit_skill_triggers=explicit_skill_triggers,
            subagent_names=subagent_names,
        )

    async def list_available_skill_names(
        self,
        *,
        profile_id: str,
        assets: ContextAssets | None = None,
    ) -> set[str]:
        """Return available skill names for one profile."""

        resolved_assets = assets or await self.collect_assets(profile_id=profile_id)
        return {item.name for item in resolved_assets.skills if item.available}

    async def list_available_skill_triggers(
        self,
        *,
        profile_id: str,
        assets: ContextAssets | None = None,
    ) -> dict[str, str]:
        """Return lowercase trigger->canonical skill mapping for names and aliases."""

        resolved_assets = assets or await self.collect_assets(profile_id=profile_id)
        return dict(resolved_assets.skill_triggers)

    async def list_available_subagent_names(
        self,
        *,
        profile_id: str,
        assets: ContextAssets | None = None,
    ) -> set[str]:
        """Return available subagent names for one profile."""

        resolved_assets = assets or await self.collect_assets(profile_id=profile_id)
        return set(resolved_assets.subagent_names)

    async def _explicit_skills_block(
        self,
        *,
        profile_id: str,
        explicit_skill_names: set[str] | None,
    ) -> str:
        """Render markdown bodies for explicitly requested skills."""

        normalized_names = self._normalize_requested_names(explicit_skill_names)
        if not normalized_names:
            return "- None."

        async def _load_one(name: str) -> str:
            try:
                body = await self._skills.load_skill(name=name, profile_id=profile_id)
            except (FileNotFoundError, ValueError):
                return f"## {name}\nUnavailable in current profile context."
            return f"## {name}\n{body.strip()}"

        parts = await asyncio.gather(*(_load_one(name) for name in normalized_names))
        return "\n\n".join(parts)

    async def _explicit_subagents_block(
        self,
        *,
        profile_id: str,
        explicit_subagent_names: set[str] | None,
    ) -> str:
        """Render markdown bodies for explicitly requested subagents."""

        normalized_names = self._normalize_requested_names(explicit_subagent_names)
        if not normalized_names:
            return "- None."

        async def _load_one(name: str) -> str:
            try:
                body = await self._subagents.load_subagent_markdown(
                    name=name, profile_id=profile_id
                )
            except (FileNotFoundError, ValueError):
                return f"## {name}\nUnavailable in current profile context."
            return f"## {name}\n{body.strip()}"

        parts = await asyncio.gather(*(_load_one(name) for name in normalized_names))
        return "\n\n".join(parts)

    async def _read_bootstrap_files(self, profile_id: str) -> str:
        parts: list[str] = []
        profile_bootstrap_dir = get_profile_runtime_config_service(self._settings).bootstrap_dir(
            profile_id
        )
        for filename in self._settings.bootstrap_files:
            file_path = self._settings.bootstrap_dir / filename
            if await asyncio.to_thread(file_path.exists):
                parts.append(await self._format_file(file_path, title=file_path.name))
            profile_path = profile_bootstrap_dir / filename
            if await asyncio.to_thread(profile_path.exists):
                parts.append(
                    await self._format_file(profile_path, title=f"Profile {profile_path.name}")
                )
        return "\n\n".join(parts)

    @staticmethod
    async def _format_file(file_path: Path, *, title: str) -> str:
        return await asyncio.to_thread(ContextBuilder._format_file_sync, file_path, title)

    @staticmethod
    def _prompt_overlay_block(prompt_overlay: str | None) -> str:
        """Render trusted binding-local prompt overlay block."""

        normalized = (prompt_overlay or "").strip()
        if not normalized:
            return "- None."
        return normalized

    @staticmethod
    def _trusted_runtime_notes_block(trusted_runtime_notes: str | None) -> str:
        """Render trusted runtime carryover block."""

        normalized = (trusted_runtime_notes or "").strip()
        if not normalized:
            return "- None."
        return normalized

    @staticmethod
    def _runtime_metadata_block(runtime_metadata: dict[str, Any] | None) -> str:
        """Render runtime metadata block."""

        if not runtime_metadata:
            return "# Runtime Metadata (untrusted)\n{}"
        sanitized_runtime_metadata = ContextBuilder._sanitize_runtime_metadata_for_model(
            runtime_metadata,
        )
        if not sanitized_runtime_metadata:
            return "# Runtime Metadata (untrusted)\n{}"
        payload = json.dumps(
            sanitized_runtime_metadata,
            ensure_ascii=True,
            sort_keys=True,
        )
        return f"# Runtime Metadata (untrusted)\n{payload}"

    @staticmethod
    def _sanitize_runtime_metadata_for_model(
        runtime_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Strip internal control metadata that should not be shown to the LLM."""

        excluded_keys = {
            "planning",
            "subagent_task",
        }
        return {
            str(key): value for key, value in runtime_metadata.items() if key not in excluded_keys
        }

    @staticmethod
    def _skills_summary_block(
        *,
        skills: list[SkillInfo],
        relevant_skill_names: set[str] | None,
    ) -> str:
        """Render deterministic skill name/description table for available skills."""

        relevant = None
        if relevant_skill_names:
            relevant = {name.strip() for name in relevant_skill_names if name.strip()}

        rows: list[str] = []
        for item in skills:
            if not item.available:
                continue
            if relevant is not None and item.name not in relevant:
                continue
            summary = item.summary or "No summary provided."
            safe_summary = summary.replace("|", "\\|")
            rows.append(f"| `{item.name}` | {safe_summary} |")

        if not rows:
            return "- No available skills."
        return "\n".join(
            [
                "| Skill | Description |",
                "| --- | --- |",
                *rows,
            ]
        )

    @staticmethod
    async def _subagents_summary_block(
        *,
        subagents: list[SubagentInfo],
        relevant_subagent_names: set[str] | None,
    ) -> str:
        """Render deterministic bullet summary for available subagents."""

        relevant = None
        if relevant_subagent_names:
            relevant = set(ContextBuilder._normalize_requested_names(relevant_subagent_names))

        selected_items: list[SubagentInfo] = []
        for item in subagents:
            if relevant is not None and item.name not in relevant:
                continue
            selected_items.append(item)

        summaries = await asyncio.gather(
            *(ContextBuilder._extract_subagent_summary(item.path) for item in selected_items)
        )
        lines: list[str] = []
        for item, summary in zip(selected_items, summaries, strict=True):
            summary = summary or "No summary provided."
            lines.append(f"- {item.name}: {summary}")

        if not lines:
            return "- No available subagents."
        return "\n".join(lines)

    @staticmethod
    def _selected_skill_cards_block(
        *,
        skills: list[SkillInfo],
        selected_skill_names: set[str] | None,
    ) -> str:
        """Render compact machine-readable cards for selected skills only."""

        if not selected_skill_names:
            return "- None."
        selected = {name.strip() for name in selected_skill_names if name and name.strip()}
        if not selected:
            return "- None."

        parts: list[str] = []
        for item in skills:
            if item.name not in selected:
                continue
            manifest = item.manifest
            aliases = ", ".join(manifest.aliases) if manifest.aliases else "-"
            triggers = ", ".join(manifest.triggers) if manifest.triggers else "-"
            tools = ", ".join(manifest.tool_names) if manifest.tool_names else "-"
            apps = ", ".join(manifest.app_names) if manifest.app_names else "-"
            requires_bins = ", ".join(manifest.requires_bins) if manifest.requires_bins else "-"
            requires_env = ", ".join(manifest.requires_env) if manifest.requires_env else "-"
            requires_python = (
                ", ".join(manifest.requires_python_packages)
                if manifest.requires_python_packages
                else "-"
            )
            preferred = (
                ", ".join(manifest.preferred_tool_order) if manifest.preferred_tool_order else "-"
            )
            availability = "available" if item.available else "unavailable"
            manifest_errors = ", ".join(item.manifest_errors) if item.manifest_errors else "-"
            missing_requirements = (
                ", ".join(item.missing_requirements) if item.missing_requirements else "-"
            )
            summary = item.summary or "No summary provided."
            parts.append(
                "\n".join(
                    [
                        f"## {item.name}",
                        f"- summary: {summary}",
                        f"- availability: {availability}",
                        f"- aliases: {aliases}",
                        f"- triggers: {triggers}",
                        f"- execution_mode: {manifest.execution_mode}",
                        f"- tools: {tools}",
                        f"- apps: {apps}",
                        f"- preferred_tool_order: {preferred}",
                        f"- requires_bins: {requires_bins}",
                        f"- requires_env: {requires_env}",
                        f"- requires_python_packages: {requires_python}",
                        f"- manifest_errors: {manifest_errors}",
                        f"- missing_requirements: {missing_requirements}",
                    ]
                )
            )
        if not parts:
            return "- None."
        return "\n\n".join(parts)

    @staticmethod
    async def _extract_subagent_summary(path: Path) -> str:
        """Extract deterministic one-line summary from one subagent markdown body."""

        return await asyncio.to_thread(ContextBuilder._extract_subagent_summary_sync, path)

    @staticmethod
    def _format_file_sync(file_path: Path, title: str) -> str:
        signature = ContextBuilder._path_signature(file_path)
        if signature is None:
            raise FileNotFoundError(file_path)
        cache_key = (str(file_path.resolve()), title)
        with _CONTEXT_CACHE_LOCK:
            cached = _FORMATTED_FILE_CACHE.get(cache_key)
            if cached is not None and cached[0] == signature:
                return cached[1]
        formatted = f"## {title}\n{file_path.read_text(encoding='utf-8').strip()}"
        with _CONTEXT_CACHE_LOCK:
            _FORMATTED_FILE_CACHE[cache_key] = (signature, formatted)
        return formatted

    @staticmethod
    def _extract_subagent_summary_sync(path: Path) -> str:
        signature = ContextBuilder._path_signature(path)
        if signature is None:
            return ""
        cache_key = str(path.resolve())
        with _CONTEXT_CACHE_LOCK:
            cached = _SUBAGENT_SUMMARY_CACHE.get(cache_key)
            if cached is not None and cached[0] == signature:
                return cached[1]
        content = path.read_text(encoding="utf-8")
        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("---"):
                continue
            if line.startswith("#"):
                text = line.lstrip("#").strip()
                if text:
                    summary = " ".join(text.split())[:_SUBAGENT_SUMMARY_MAX_CHARS]
                    break
                continue
            summary = " ".join(line.split())[:_SUBAGENT_SUMMARY_MAX_CHARS]
            break
        else:
            summary = ""
        with _CONTEXT_CACHE_LOCK:
            _SUBAGENT_SUMMARY_CACHE[cache_key] = (signature, summary)
        return summary

    @staticmethod
    def _normalize_requested_names(names: set[str] | None) -> list[str]:
        return sorted({name.strip() for name in (names or set()) if name and name.strip()})

    @staticmethod
    def _path_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat_result = path.stat()
        except OSError:
            return None
        return (stat_result.st_mtime_ns, stat_result.st_size)
