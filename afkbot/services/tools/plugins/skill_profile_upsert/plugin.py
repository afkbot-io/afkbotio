"""Tool plugin for skill.profile.upsert."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.policy import ProfileFilesLockedError
from afkbot.services.skills import get_profile_skill_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.settings import Settings


class SkillProfileUpsertParams(RoutedToolParameters):
    """Parameters for skill.profile.upsert tool."""

    name: str = Field(min_length=1, max_length=128)
    markdown: str = Field(min_length=1, max_length=200_000)


class SkillProfileUpsertTool(ToolBase):
    """Create or update one custom profile skill markdown file."""

    name = "skill.profile.upsert"
    description = "Create or update one profile SKILL.md file (name is normalized to a safe slug)."
    parameters_model = SkillProfileUpsertParams
    required_skill = "skill-creator"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SkillProfileUpsertParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")
        service = get_profile_skill_service(self._settings)
        try:
            item = await service.upsert(
                profile_id=ctx.profile_id,
                name=payload.name,
                content=payload.markdown,
            )
            return ToolResult(ok=True, payload={"skill": item.model_dump(mode="json", exclude_none=True)})
        except ProfileFilesLockedError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except ValueError as exc:
            return ToolResult.error(error_code="invalid_skill_name", reason=str(exc))


def create_tool(settings: Settings) -> ToolBase:
    """Create skill.profile.upsert tool instance."""

    return SkillProfileUpsertTool(settings=settings)
