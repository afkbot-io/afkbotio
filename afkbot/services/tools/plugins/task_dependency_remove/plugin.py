"""Tool plugin for task.dependency.remove."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import resolve_task_tool_actor
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskDependencyRemoveParams(ToolParameters):
    """Parameters for task.dependency.remove tool."""

    task_id: str = Field(min_length=1, max_length=64)
    depends_on_task_id: str = Field(min_length=1, max_length=64)


class TaskDependencyRemoveTool(ToolBase):
    """Remove one dependency edge from a task."""

    name = "task.dependency.remove"
    description = "Remove one Task Flow dependency edge."
    parameters_model = TaskDependencyRemoveParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskDependencyRemoveParams)
            else TaskDependencyRemoveParams.model_validate(params)
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
            actor = resolve_task_tool_actor(ctx)
            deleted = await service.remove_dependency(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                depends_on_task_id=payload.depends_on_task_id,
                actor_type=actor.actor_type,
                actor_ref=actor.actor_ref,
                actor_session_id=actor.actor_session_id,
            )
            return ToolResult(
                ok=True,
                payload={
                    "deleted": deleted,
                    "task_id": payload.task_id,
                    "depends_on_task_id": payload.depends_on_task_id,
                },
            )
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.dependency.remove tool instance."""

    return TaskDependencyRemoveTool(settings=settings)
