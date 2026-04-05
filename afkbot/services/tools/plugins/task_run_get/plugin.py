"""Tool plugin for task.run.get."""

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


class TaskRunGetParams(ToolParameters):
    """Parameters for task.run.get tool."""

    task_run_id: int = Field(ge=1)


class TaskRunGetTool(ToolBase):
    """Get one task execution attempt."""

    name = "task.run.get"
    description = "Get one Task Flow execution attempt."
    parameters_model = TaskRunGetParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskRunGetParams) else TaskRunGetParams.model_validate(params)
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
            item = await service.get_task_run(
                profile_id=target_profile_id,
                task_run_id=payload.task_run_id,
            )
            return ToolResult(ok=True, payload={"task_run": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.run.get tool instance."""

    return TaskRunGetTool(settings=settings)
