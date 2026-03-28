"""Session-scoped runtime catalog for desktop app shells and mention palettes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from afkbot.services.profile_runtime.service import get_profile_service
from afkbot.services.skills.markdown import extract_summary
from afkbot.services.skills.skills import SkillInfo, SkillLoader
from afkbot.services.subagents.contracts import SubagentInfo
from afkbot.services.subagents.loader import SubagentLoader
from afkbot.settings import Settings

__all__ = [
    "AppCatalogMention",
    "AppCatalogResponse",
    "AppCatalogRuntime",
    "AppCatalogService",
    "get_app_catalog_service",
]

_SERVICES_BY_ROOT: dict[str, tuple[str, "AppCatalogService"]] = {}


class AppCatalogRuntime(BaseModel):
    """Compact runtime metadata rendered by the desktop shell."""

    model_config = ConfigDict(extra="forbid")

    llm_provider: str
    llm_model: str
    thinking_level: str
    planning_mode: str
    policy_preset: str
    file_access_mode: str
    capabilities: tuple[str, ...] = ()


class AppCatalogMention(BaseModel):
    """One mention suggestion shown in the desktop composer."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["skill", "subagent"]
    slug: str
    title: str
    description: str
    aliases: tuple[str, ...] = ()
    origin: Literal["core", "profile"]
    available: bool = True


class AppCatalogResponse(BaseModel):
    """Normalized runtime catalog for one authenticated profile/session."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str
    profile_name: str
    session_id: str
    runtime: AppCatalogRuntime
    mentions: tuple[AppCatalogMention, ...]


class AppCatalogService:
    """Build one normalized runtime catalog from existing profile/skill services."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._skills = SkillLoader(settings)
        self._subagents = SubagentLoader(settings)

    async def get_catalog(self, *, profile_id: str, session_id: str) -> AppCatalogResponse:
        """Return runtime metadata and mentionable entities for one chat scope."""

        profile = await get_profile_service(self._settings).get(profile_id=profile_id)
        mentions = [
            *[self._map_skill(item) for item in await self._skills.list_skills(profile_id)],
            *[self._map_subagent(item) for item in await self._subagents.list_subagents(profile_id)],
        ]
        mentions.sort(key=_mention_sort_key)
        return AppCatalogResponse(
            profile_id=profile.id,
            profile_name=profile.name,
            session_id=session_id,
            runtime=AppCatalogRuntime(
                llm_provider=profile.effective_runtime.llm_provider,
                llm_model=profile.effective_runtime.llm_model,
                thinking_level=profile.effective_runtime.llm_thinking_level,
                planning_mode=profile.effective_runtime.chat_planning_mode,
                policy_preset=profile.policy.preset,
                file_access_mode=profile.policy.file_access_mode,
                capabilities=profile.policy.capabilities,
            ),
            mentions=tuple(mentions),
        )

    def _map_skill(self, item: SkillInfo) -> AppCatalogMention:
        return AppCatalogMention(
            kind="skill",
            slug=item.name,
            title=_display_title(item.name),
            description=item.summary or _display_title(item.name),
            aliases=item.aliases,
            origin=_normalize_origin(item.origin),
            available=item.available,
        )

    def _map_subagent(self, item: SubagentInfo) -> AppCatalogMention:
        return AppCatalogMention(
            kind="subagent",
            slug=item.name,
            title=_display_title(item.name),
            description=_read_markdown_summary(item.path) or _display_title(item.name),
            origin=_normalize_origin(item.origin),
            available=True,
        )


def get_app_catalog_service(settings: Settings) -> AppCatalogService:
    """Return cached runtime catalog service for one workspace root."""

    key = str(settings.root_dir.resolve())
    signature = _settings_signature(settings)
    cached = _SERVICES_BY_ROOT.get(key)
    if cached is None or cached[0] != signature:
        service = AppCatalogService(settings)
        _SERVICES_BY_ROOT[key] = (signature, service)
        return service
    return cached[1]


def _settings_signature(settings: Settings) -> str:
    """Return stable cache signature for one settings snapshot."""

    return json.dumps(
        {
            "root_dir": str(settings.root_dir.resolve()),
            "profiles_dir": str(settings.profiles_dir.resolve()),
            "skills_dir": str(settings.skills_dir.resolve()),
            "subagents_dir": str(settings.subagents_dir.resolve()),
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "llm_thinking_level": settings.llm_thinking_level,
            "chat_planning_mode": settings.chat_planning_mode,
        },
        sort_keys=True,
        ensure_ascii=True,
    )


def _display_title(slug: str) -> str:
    normalized = " ".join(part for part in slug.strip().split("-") if part)
    if not normalized:
        return slug.strip()
    return " ".join(part.capitalize() for part in normalized.split())


def _normalize_origin(origin: str) -> Literal["core", "profile"]:
    return "profile" if origin == "profile" else "core"


def _read_markdown_summary(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return extract_summary(content)


def _mention_sort_key(item: AppCatalogMention) -> tuple[int, int, str]:
    return (
        0 if item.available else 1,
        0 if item.kind == "skill" else 1,
        item.slug,
    )
