"""Tool plugin for task.dependency.list."""

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


class TaskDependencyListParams(ToolParameters):
    """Parameters for task.dependency.list tool."""

    task_id: str = Field(min_length=1, max_length=64)


class TaskDependencyListTool(ToolBase):
    """List dependency edges for one task."""

    name = "task.dependency.list"
    description = "List Task Flow dependency edges for one task."
    parameters_model = TaskDependencyListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskDependencyListParams)
            else TaskDependencyListParams.model_validate(params)
        )
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
            items = await service.list_dependencies(
                profile_id=target_profile_id,
                task_id=payload.task_id,
            )
            return ToolResult(
                ok=True,
                payload={"dependencies": [item.model_dump(mode="json") for item in items]},
            )
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.dependency.list tool instance."""

    return TaskDependencyListTool(settings=settings)
