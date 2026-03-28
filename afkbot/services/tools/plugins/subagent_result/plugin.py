"""Tool plugin for subagent.result."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from afkbot.services.subagents import get_subagent_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters, build_tool_parameters
from afkbot.settings import Settings


class SubagentResultParams(ToolParameters):
    """Parameters for subagent.result tool."""

    task_id: str = Field(min_length=1)


class SubagentResultTool(ToolBase):
    """Get result for a subagent task."""

    name = "subagent.result"
    description = "Return final or current result for subagent task."
    parameters_model = SubagentResultParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def parse_params(
        self,
        raw_params: Mapping[str, Any],
        *,
        default_timeout_sec: int,
        max_timeout_sec: int,
    ) -> ToolParameters:
        """Use subagent timeout settings for result tool constraints."""

        _ = default_timeout_sec, max_timeout_sec
        return build_tool_parameters(
            self.parameters_model,
            raw_params,
            default_timeout_sec=self._settings.subagent_timeout_default_sec,
            max_timeout_sec=self._settings.subagent_timeout_max_sec,
        )

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        result_params = SubagentResultParams.model_validate(params.model_dump())
        service = get_subagent_service(self._settings)
        try:
            response = await service.result(
                task_id=result_params.task_id,
                profile_id=ctx.profile_id,
                session_id=ctx.session_id,
            )
            ok = response.status in {"completed", "running"}
            return ToolResult(
                ok=ok,
                payload=response.model_dump(),
                error_code=response.error_code,
                reason=response.reason,
            )
        except KeyError:
            return ToolResult.error(
                error_code="subagent_task_not_found",
                reason=f"Subagent task not found: {result_params.task_id}",
            )
        except PermissionError:
            return ToolResult.error(
                error_code="subagent_task_not_found",
                reason=f"Subagent task not found: {result_params.task_id}",
            )


def create_tool(settings: Settings) -> ToolBase:
    """Create subagent.result tool instance."""

    return SubagentResultTool(settings=settings)
