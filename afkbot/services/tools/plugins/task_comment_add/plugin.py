"""Tool plugin for task.comment.add."""

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


class TaskCommentAddParams(ToolParameters):
    """Parameters for task.comment.add tool."""

    task_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1)
    comment_type: str = Field(default="note", min_length=1, max_length=64)
    task_run_id: int | None = None


class TaskCommentAddTool(ToolBase):
    """Append one comment to a Task Flow task."""

    name = "task.comment.add"
    description = "Append one comment to a Task Flow task."
    parameters_model = TaskCommentAddParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskCommentAddParams) else TaskCommentAddParams.model_validate(params)
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
            item = await service.add_task_comment(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                message=payload.message,
                actor_type="ai_profile",
                actor_ref=ctx.profile_id,
                comment_type=payload.comment_type,
                task_run_id=payload.task_run_id,
            )
            return ToolResult(ok=True, payload={"task_comment": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.comment.add tool instance."""

    return TaskCommentAddTool(settings=settings)
