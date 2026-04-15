"""Tool plugin for task.review.approve."""

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


class TaskReviewApproveParams(ToolParameters):
    """Parameters for task.review.approve tool."""

    task_id: str = Field(min_length=1, max_length=64)
    actor_type: str | None = Field(default=None, max_length=32)
    actor_ref: str | None = Field(default=None, max_length=255)


class TaskReviewApproveTool(ToolBase):
    """Approve one review task."""

    name = "task.review.approve"
    description = "Approve one Task Flow task currently in review."
    parameters_model = TaskReviewApproveParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params if isinstance(params, TaskReviewApproveParams) else TaskReviewApproveParams.model_validate(params)
        )
        explicit_actor_type = str(payload.actor_type or "").strip() or None
        explicit_actor_ref = str(payload.actor_ref or "").strip() or None
        if (explicit_actor_type is not None or explicit_actor_ref is not None) and (
            explicit_actor_type != "ai_profile" or explicit_actor_ref != ctx.profile_id
        ):
            return ToolResult.error(
                error_code="task_review_actor_forbidden",
                reason="Review actions can only be performed by the current AI profile",
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
            item = await service.approve_review_task(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                actor_type="ai_profile",
                actor_ref=ctx.profile_id,
                actor_session_id=ctx.session_id,
            )
            return ToolResult(ok=True, payload={"task": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.review.approve tool instance."""

    return TaskReviewApproveTool(settings=settings)
