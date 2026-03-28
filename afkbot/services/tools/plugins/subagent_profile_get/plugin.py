"""Tool plugin for subagent.profile.get."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.subagents import get_profile_subagent_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.settings import Settings


class SubagentProfileGetParams(RoutedToolParameters):
    """Parameters for subagent.profile.get tool."""

    name: str = Field(min_length=1, max_length=128)


class SubagentProfileGetTool(ToolBase):
    """Read one custom profile subagent markdown file."""

    name = "subagent.profile.get"
    description = "Read one profile subagent markdown by name (localized labels are normalized to safe slug)."
    parameters_model = SubagentProfileGetParams
    required_skill = "subagent-manager"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SubagentProfileGetParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")
        service = get_profile_subagent_service(self._settings)
        try:
            item = await service.get(profile_id=ctx.profile_id, name=payload.name)
            return ToolResult(ok=True, payload={"subagent": item.model_dump(mode="json", exclude_none=True)})
        except FileNotFoundError as exc:
            return ToolResult.error(error_code="profile_subagent_not_found", reason=str(exc))
        except ValueError as exc:
            return ToolResult.error(error_code="invalid_subagent_name", reason=str(exc))


def create_tool(settings: Settings) -> ToolBase:
    """Create subagent.profile.get tool instance."""

    return SubagentProfileGetTool(settings=settings)
