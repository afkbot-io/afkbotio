"""Tool plugin for skill.profile.get."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from afkbot.services.skills import get_profile_skill_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.settings import Settings


class SkillProfileGetParams(RoutedToolParameters):
    """Parameters for skill.profile.get tool."""

    name: str = Field(min_length=1, max_length=128)
    scope: Literal["all", "profile", "core"] = Field(default="all")


class SkillProfileGetTool(ToolBase):
    """Read one skill markdown file by name with optional scope."""

    name = "skill.profile.get"
    description = "Read one SKILL.md by name (core/profile/all; localized labels normalized)."
    parameters_model = SkillProfileGetParams
    required_skill = "skill-creator"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SkillProfileGetParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")
        service = get_profile_skill_service(self._settings)
        try:
            item = await service.get(
                profile_id=ctx.profile_id,
                name=payload.name,
                scope=payload.scope,
            )
            return ToolResult(ok=True, payload={"skill": item.model_dump(mode="json", exclude_none=True)})
        except FileNotFoundError as exc:
            return ToolResult.error(error_code="profile_skill_not_found", reason=str(exc))
        except ValueError as exc:
            return ToolResult.error(error_code="invalid_skill_name", reason=str(exc))


def create_tool(settings: Settings) -> ToolBase:
    """Create skill.profile.get tool instance."""

    return SkillProfileGetTool(settings=settings)
