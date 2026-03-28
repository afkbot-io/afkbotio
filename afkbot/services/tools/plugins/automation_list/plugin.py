"""Tool plugin for automation.list."""

from __future__ import annotations

from afkbot.services.automations import AutomationsServiceError, get_automations_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class AutomationListParams(ToolParameters):
    """Parameters for automation.list tool."""

    include_deleted: bool = False


class AutomationListTool(ToolBase):
    """List profile automations metadata."""

    name = "automation.list"
    description = "List profile automations metadata."
    parameters_model = AutomationListParams
    requires_automation_intent = True

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = AutomationListParams.model_validate(params.model_dump())
        if payload.profile_key != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            service = get_automations_service(self._settings)
            items = await service.list(
                profile_id=ctx.profile_id,
                include_deleted=payload.include_deleted,
            )
            return ToolResult(
                ok=True,
                payload={"automations": [item.model_dump(mode="json") for item in items]},
            )
        except AutomationsServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create automation.list tool instance."""

    return AutomationListTool(settings=settings)
