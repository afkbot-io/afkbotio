"""Tool plugin for automation.update."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.automations import AutomationsServiceError, get_automations_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class AutomationUpdateParams(ToolParameters):
    """Parameters for automation.update tool."""

    id: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=255)
    prompt: str | None = None
    status: str | None = Field(default=None, max_length=32)
    cron_expr: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)
    rotate_webhook_token: bool = False


class AutomationUpdateTool(ToolBase):
    """Update one profile automation by id."""

    name = "automation.update"
    description = "Update one profile automation by id."
    parameters_model = AutomationUpdateParams
    requires_automation_intent = True

    def __init__(self, settings: Settings) -> None:
        """Store application settings used to resolve the automation service."""

        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        """Validate parameters and update one automation for the active profile."""

        payload = AutomationUpdateParams.model_validate(params.model_dump())
        if payload.profile_key != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            service = get_automations_service(self._settings)
            item = await service.update(
                profile_id=ctx.profile_id,
                automation_id=payload.id,
                name=payload.name,
                prompt=payload.prompt,
                status=payload.status,
                cron_expr=payload.cron_expr,
                timezone_name=payload.timezone,
                rotate_webhook_token=payload.rotate_webhook_token,
            )
            return ToolResult(ok=True, payload={"automation": item.model_dump(mode="json")})
        except AutomationsServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create automation.update tool instance."""

    return AutomationUpdateTool(settings=settings)
