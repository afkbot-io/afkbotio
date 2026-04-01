"""Tool plugin for automation.create."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from afkbot.services.automations import AutomationsServiceError, get_automations_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.settings import Settings


class AutomationCreateParams(ToolParameters):
    """Parameters for automation.create tool."""

    name: str = Field(min_length=1, max_length=255)
    prompt: str = Field(
        min_length=1,
        description=(
            "Self-contained automation task prompt. Rewrite the user's request into a standalone "
            "instruction that explains what the automation should do and what outcome is expected. "
            "Do not include schedule details here."
        ),
    )
    trigger_type: Literal["cron", "webhook"]
    cron_expr: str | None = Field(default=None, max_length=64)
    timezone: str | None = Field(default=None, max_length=64)


class AutomationCreateTool(ToolBase):
    """Create profile automation with cron or webhook trigger."""

    name = "automation.create"
    description = "Create profile automation for cron or webhook trigger."
    parameters_model = AutomationCreateParams
    requires_automation_intent = True

    def __init__(self, settings: Settings) -> None:
        """Store application settings used to resolve the automation service."""

        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        """Validate parameters and create one automation for the active profile."""

        payload = AutomationCreateParams.model_validate(params.model_dump())
        if payload.profile_key != ctx.profile_id:
            return ToolResult.error(error_code="profile_not_found", reason="Profile not found")

        try:
            service = get_automations_service(self._settings)
            if payload.trigger_type == "cron":
                if payload.cron_expr is None:
                    return ToolResult.error(
                        error_code="invalid_cron_expr",
                        reason="cron_expr is required for cron trigger",
                    )
                automation = await service.create_cron(
                    profile_id=ctx.profile_id,
                    name=payload.name,
                    prompt=payload.prompt,
                    cron_expr=payload.cron_expr,
                    timezone_name=payload.timezone or "UTC",
                )
            else:
                automation = await service.create_webhook(
                    profile_id=ctx.profile_id,
                    name=payload.name,
                    prompt=payload.prompt,
                )
            return ToolResult(ok=True, payload={"automation": automation.model_dump(mode="json")})
        except AutomationsServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create automation.create tool instance."""

    return AutomationCreateTool(settings=settings)
