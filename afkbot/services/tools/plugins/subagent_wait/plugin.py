"""Tool plugin for subagent.wait."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from afkbot.services.subagents import get_subagent_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters, build_tool_parameters
from afkbot.settings import Settings


class SubagentWaitParams(ToolParameters):
    """Parameters for subagent.wait tool."""

    task_id: str = Field(min_length=1)


class SubagentWaitTool(ToolBase):
    """Wait for a subagent task completion with bounded timeout."""

    name = "subagent.wait"
    description = "Wait for subagent completion and return current status."
    parameters_model = SubagentWaitParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse_params(
        self,
        raw_params: Mapping[str, Any],
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> ToolParameters:
        """Use subagent timeout settings for wait tool constraints."""

        _ = default_timeout_sec, max_timeout_sec
        return build_tool_parameters(
            self.parameters_model,
            raw_params,
            default_timeout_sec=self._settings.subagent_wait_default_sec,
            max_timeout_sec=self._settings.subagent_wait_max_sec,
        )

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        wait_params = SubagentWaitParams.model_validate(params.model_dump())
        service = get_subagent_service(self._settings)
        try:
            response = await service.wait(
                task_id=wait_params.task_id,
                timeout_sec=wait_params.timeout_sec,
                profile_id=ctx.profile_id,
                session_id=ctx.session_id,
            )
            return ToolResult(ok=True, payload=response.model_dump())
        except KeyError:
            return ToolResult.error(
                error_code="subagent_task_not_found",
                reason=f"Subagent task not found: {wait_params.task_id}",
            )
        except PermissionError:
            return ToolResult.error(
                error_code="subagent_task_not_found",
                reason=f"Subagent task not found: {wait_params.task_id}",
            )


def create_tool(settings: Settings) -> ToolBase:
    """Create subagent.wait tool instance."""

    return SubagentWaitTool(settings=settings)
