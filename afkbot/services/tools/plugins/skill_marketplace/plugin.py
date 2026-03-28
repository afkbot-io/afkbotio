"""Tool plugins for skill marketplace list/install actions."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.policy import ProfileFilesLockedError
from afkbot.services.skills import (
    SkillMarketplaceError,
    SkillMarketplaceListResult,
    SkillMarketplaceSourceStats,
    get_skill_marketplace_service,
)
from afkbot.services.skills.marketplace_payloads import (
    marketplace_install_record_to_payload,
    marketplace_list_item_to_payload,
    marketplace_source_stats_to_payload,
)
from afkbot.services.skills.marketplace_popularity import format_compact_count
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.settings import Settings


class SkillMarketplaceListParams(RoutedToolParameters):
    """Parameters for skill.marketplace.list."""

    source: str = Field(default="default", min_length=1, max_length=4096)
    limit: int = Field(default=50, ge=1, le=200)


class SkillMarketplaceSearchParams(RoutedToolParameters):
    """Parameters for skill.marketplace.search."""

    source: str = Field(default="default", min_length=1, max_length=4096)
    query: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=50, ge=1, le=200)


class SkillMarketplaceInstallParams(RoutedToolParameters):
    """Parameters for skill.marketplace.install."""

    source: str = Field(default="default", min_length=1, max_length=4096)
    skill: str | None = Field(default=None, min_length=1, max_length=128)
    target_name: str | None = Field(default=None, min_length=1, max_length=128)
    overwrite: bool = False


class SkillMarketplaceListTool(ToolBase):
    """List available skills from one marketplace source."""

    name = "skill.marketplace.list"
    description = "List installable skills from one marketplace source."
    parameters_model = SkillMarketplaceListParams
    required_skill = "skill-creator"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SkillMarketplaceListParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        service = get_skill_marketplace_service(self._settings)
        try:
            listing = await service.list_source(
                source=payload.source,
                limit=payload.limit,
                profile_id=ctx.profile_id,
            )
            return ToolResult(
                ok=True,
                payload={
                    "source": payload.source,
                    "resolved_source": listing.source,
                    "source_stats": marketplace_source_stats_to_payload(listing.source_stats),
                    "skills": [marketplace_list_item_to_payload(item) for item in listing.items],
                    "display_text": _render_marketplace_display_text(
                        listing=listing,
                        requested_source=payload.source,
                    ),
                },
            )
        except SkillMarketplaceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult.error(
                error_code="skill_marketplace_list_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )


class SkillMarketplaceSearchTool(ToolBase):
    """Search installable skills from one marketplace source."""

    name = "skill.marketplace.search"
    description = "Search installable skills from one marketplace source by query."
    parameters_model = SkillMarketplaceSearchParams
    required_skill = "skill-creator"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SkillMarketplaceSearchParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        service = get_skill_marketplace_service(self._settings)
        try:
            listing = await service.search_source(
                source=payload.source,
                query=payload.query,
                limit=payload.limit,
                profile_id=ctx.profile_id,
            )
            return ToolResult(
                ok=True,
                payload={
                    "source": payload.source,
                    "resolved_source": listing.source,
                    "source_stats": marketplace_source_stats_to_payload(listing.source_stats),
                    "skills": [marketplace_list_item_to_payload(item) for item in listing.items],
                    "display_text": _render_marketplace_display_text(
                        listing=listing,
                        requested_source=payload.source,
                        query=payload.query,
                    ),
                },
            )
        except SkillMarketplaceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult.error(
                error_code="skill_marketplace_search_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )


class SkillMarketplaceInstallTool(ToolBase):
    """Install one skill from marketplace source into profile-local skills."""

    name = "skill.marketplace.install"
    description = "Install one marketplace skill into profile-local skills."
    parameters_model = SkillMarketplaceInstallParams
    required_skill = "skill-creator"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SkillMarketplaceInstallParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        service = get_skill_marketplace_service(self._settings)
        try:
            record = await service.install(
                profile_id=ctx.profile_id,
                source=payload.source,
                skill=payload.skill,
                target_name=payload.target_name,
                overwrite=payload.overwrite,
            )
            return ToolResult(ok=True, payload={"skill": marketplace_install_record_to_payload(record)})
        except ProfileFilesLockedError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except SkillMarketplaceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except Exception as exc:  # pragma: no cover - defensive fallback
            return ToolResult.error(
                error_code="skill_marketplace_install_failed",
                reason=f"{exc.__class__.__name__}: {exc}",
            )

def _render_marketplace_display_text(
    *,
    listing: SkillMarketplaceListResult,
    requested_source: str,
    query: str | None = None,
) -> str:
    """Render deterministic marketplace output without another LLM pass."""

    items = list(listing.items)
    if not items:
        if query:
            return f"No marketplace skills found for query `{query}` in source `{requested_source}`."
        return f"No marketplace skills found in source `{requested_source}`."
    heading = (
        f"Marketplace skills for `{query}` in `{requested_source}`:"
        if query
        else f"Marketplace skills in `{requested_source}`:"
    )
    lines = [heading]
    source_stats_line = _render_source_stats(listing.source_stats)
    if source_stats_line:
        lines.append(source_stats_line)
    for item in items:
        details: list[str] = []
        if item.rank is not None:
            details.append(f"#{item.rank}")
        if item.installs_display:
            details.append(f"{item.installs_display} installs")
        if item.installed:
            if item.installed_name and item.installed_name != item.name:
                details.append(f"installed as `{item.installed_name}`")
            else:
                details.append("installed")
        line = f"- `{item.name}`"
        if details:
            line += " | " + " | ".join(details)
        summary = item.summary.strip()
        if summary:
            line += f" | {summary}"
        lines.append(line)
    return "\n".join(lines)


def _render_source_stats(source_stats: SkillMarketplaceSourceStats) -> str:
    details: list[str] = []
    if source_stats.total_installs is not None:
        total_installs = source_stats.total_installs_display or format_compact_count(
            source_stats.total_installs
        )
        details.append(f"ranking: {total_installs} total installs")
    if source_stats.repo_stars is not None:
        details.append(f"GitHub: {format_compact_count(source_stats.repo_stars)} stars")
    if source_stats.repo_forks is not None:
        details.append(f"{format_compact_count(source_stats.repo_forks)} forks")
    if source_stats.repo_watchers is not None:
        details.append(f"{format_compact_count(source_stats.repo_watchers)} watchers")
    if not details:
        return ""
    return "  " + " | ".join(details)


def create_list_tool(settings: Settings) -> ToolBase:
    """Create skill.marketplace.list tool instance."""

    return SkillMarketplaceListTool(settings=settings)


def create_install_tool(settings: Settings) -> ToolBase:
    """Create skill.marketplace.install tool instance."""

    return SkillMarketplaceInstallTool(settings=settings)


def create_search_tool(settings: Settings) -> ToolBase:
    """Create skill.marketplace.search tool instance."""

    return SkillMarketplaceSearchTool(settings=settings)
