"""Tool plugin for subagent.profile.list."""

from __future__ import annotations

from afkbot.services.subagents import get_profile_subagent_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import RoutedToolParameters, ToolParameters
from afkbot.settings import Settings


class SubagentProfileListParams(RoutedToolParameters):
    """Parameters for subagent.profile.list tool."""


class SubagentProfileListTool(ToolBase):
    """List profile-defined custom subagent markdown files."""

    name = "subagent.profile.list"
    description = "List custom subagent markdown files from current profile."
    parameters_model = SubagentProfileListParams
    required_skill = "subagent-manager"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = SubagentProfileListParams.model_validate(params.model_dump())
        if payload.effective_profile_id != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        service = get_profile_subagent_service(self._settings)
        items = await service.list(profile_id=ctx.profile_id)
        return ToolResult(
            ok=True,
            payload={"subagents": [item.model_dump(mode="json", exclude_none=True) for item in items]},
        )


def create_tool(settings: Settings) -> ToolBase:
    """Create subagent.profile.list tool instance."""

    return SubagentProfileListTool(settings=settings)

