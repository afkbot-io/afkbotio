"""Tool plugin for skill.profile.list."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from afkbot.services.skills import get_profile_skill_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.settings import Settings


class SkillProfileListParams(RoutedToolParameters):
    """Parameters for skill.profile.list tool."""

    scope: Literal["all", "profile", "core"] = Field(default="all")
    include_unavailable: bool = False


class SkillProfileListTool(ToolBase):
    """List skills visible in profile context."""

    name = "skill.profile.list"
    description = "List skills (core/profile) with optional scope filter."
    parameters_model = SkillProfileListParams
    required_skill = "skill-creator"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SkillProfileListParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        service = get_profile_skill_service(self._settings)
        items = await service.list(
            profile_id=ctx.profile_id,
            scope=payload.scope,
            include_unavailable=payload.include_unavailable,
        )
        response: dict[str, object] = {
            "scope": payload.scope,
            "skills": [item.model_dump(mode="json", exclude_none=True) for item in items],
            "display_text": _render_profile_skills_display_text(
                scope=payload.scope,
                items=items,
            ),
        }
        if payload.scope == "profile":
            core_items = await service.list(
                profile_id=ctx.profile_id,
                scope="core",
                include_unavailable=payload.include_unavailable,
            )
            response["core_skill_count"] = len(core_items)
            response["scope_note"] = (
                "scope=profile returns only profile-local skills. "
                "Built-in core skills such as telegram are excluded; use scope=all to see both."
            )
            if not items and core_items:
                response["hint"] = (
                    "No profile-local skills found. Core skills are available but excluded by scope=profile."
                )
        return ToolResult(
            ok=True,
            payload=response,
        )


def _render_profile_skills_display_text(*, scope: str, items: Sequence[object]) -> str:
    """Render deterministic skill listing for chat output."""

    if not items:
        return f"No skills found for scope `{scope}`."
    lines = [f"Skills in scope `{scope}`:"]
    for item in items:
        if not hasattr(item, "name"):
            continue
        name = str(getattr(item, "name", "")).strip()
        summary = str(getattr(item, "summary", "") or "").strip()
        suffix = f" — {summary}" if summary else ""
        lines.append(f"- `{name}`{suffix}")
    return "\n".join(lines)


def create_tool(settings: Settings) -> ToolBase:
    """Create skill.profile.list tool instance."""

    return SkillProfileListTool(settings=settings)
