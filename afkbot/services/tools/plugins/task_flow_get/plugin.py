"""Tool plugin for task.flow.get."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskFlowGetParams(ToolParameters):
    """Parameters for task.flow.get tool."""

    flow_id: str = Field(min_length=1, max_length=64)


class TaskFlowGetTool(ToolBase):
    """Fetch one Task Flow container."""

    name = "task.flow.get"
    description = "Get one Task Flow container."
    parameters_model = TaskFlowGetParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskFlowGetParams) else TaskFlowGetParams.model_validate(params)
        target_profile_id = resolve_task_target_profile(
            ctx=ctx,
            payload=payload,
            explicit_fields=set(getattr(payload, "model_fields_set", set())),
        )
        scope_error = ensure_task_target_scope(ctx=ctx, target_profile_id=target_profile_id)
        if scope_error is not None:
            return scope_error

        try:
            service = get_task_flow_service(self._settings)
            item = await service.get_flow(profile_id=target_profile_id, flow_id=payload.flow_id)
            return ToolResult(ok=True, payload={"task_flow": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.flow.get tool instance."""

    return TaskFlowGetTool(settings=settings)
