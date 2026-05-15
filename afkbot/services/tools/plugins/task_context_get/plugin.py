"""Tool plugin for task.context.get."""

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


class TaskContextGetParams(ToolParameters):
    """Parameters for task.context.get tool."""

    task_id: str = Field(min_length=1, max_length=64)
    event_limit: int = Field(default=20, ge=1, le=100)
    comment_limit: int = Field(default=10, ge=1, le=50)


class TaskContextGetTool(ToolBase):
    """Return a Task Flow context bundle for one task."""

    name = "task.context.get"
    description = (
        "Return task, flow docs, task docs, dependencies, comments, events, and delegated work."
    )
    parameters_model = TaskContextGetParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskContextGetParams)
            else TaskContextGetParams.model_validate(params)
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
            task_context = await service.build_task_context(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                event_limit=payload.event_limit,
                comment_limit=payload.comment_limit,
            )
            return ToolResult(
                ok=True, payload={"task_context": task_context.model_dump(mode="json")}
            )
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.context.get tool instance."""

    return TaskContextGetTool(settings=settings)
