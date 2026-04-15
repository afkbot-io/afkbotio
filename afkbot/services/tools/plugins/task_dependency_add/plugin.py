"""Tool plugin for task.dependency.add."""

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


class TaskDependencyAddParams(ToolParameters):
    """Parameters for task.dependency.add tool."""

    task_id: str = Field(min_length=1, max_length=64)
    depends_on_task_id: str = Field(min_length=1, max_length=64)
    satisfied_on_status: str = Field(default="completed", min_length=1, max_length=32)


class TaskDependencyAddTool(ToolBase):
    """Add one dependency edge to a task."""

    name = "task.dependency.add"
    description = "Add one Task Flow dependency edge."
    parameters_model = TaskDependencyAddParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskDependencyAddParams)
            else TaskDependencyAddParams.model_validate(params)
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
            item = await service.add_dependency(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                depends_on_task_id=payload.depends_on_task_id,
                satisfied_on_status=payload.satisfied_on_status,
                actor_type="ai_profile",
                actor_ref=ctx.profile_id,
                actor_session_id=ctx.session_id,
            )
            return ToolResult(ok=True, payload={"dependency": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.dependency.add tool instance."""

    return TaskDependencyAddTool(settings=settings)
