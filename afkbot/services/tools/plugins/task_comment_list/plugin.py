"""Tool plugin for task.comment.list."""

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


class TaskCommentListParams(ToolParameters):
    """Parameters for task.comment.list tool."""

    task_id: str = Field(min_length=1, max_length=64)
    limit: int | None = Field(default=None, ge=1, le=200)


class TaskCommentListTool(ToolBase):
    """List task comments."""

    name = "task.comment.list"
    description = "List append-only comments for one Task Flow task."
    parameters_model = TaskCommentListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskCommentListParams) else TaskCommentListParams.model_validate(params)
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
            items = await service.list_task_comments(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                limit=payload.limit,
            )
            return ToolResult(ok=True, payload={"task_comments": [item.model_dump(mode="json") for item in items]})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.comment.list tool instance."""

    return TaskCommentListTool(settings=settings)
