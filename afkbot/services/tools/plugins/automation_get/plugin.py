"""Tool plugin for automation.get."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.automations import AutomationsServiceError, get_automations_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class AutomationGetParams(ToolParameters):
    """Parameters for automation.get tool."""

    id: int = Field(ge=1)


class AutomationGetTool(ToolBase):
    """Get one profile automation metadata by id."""

    name = "automation.get"
    description = "Get one profile automation by id."
    parameters_model = AutomationGetParams
    requires_automation_intent = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = AutomationGetParams.model_validate(params.model_dump())
        if payload.profile_key != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            service = get_automations_service(self._settings)
            item = await service.get(profile_id=ctx.profile_id, automation_id=payload.id)
            return ToolResult(ok=True, payload={"automation": item.model_dump(mode="json")})
        except AutomationsServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create automation.get tool instance."""

    return AutomationGetTool(settings=settings)
