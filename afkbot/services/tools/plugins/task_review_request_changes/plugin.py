"""Tool plugin for task.review.request_changes."""

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


class TaskReviewRequestChangesParams(ToolParameters):
    """Parameters for task.review.request_changes tool."""

    task_id: str = Field(min_length=1, max_length=64)
    reason_text: str = Field(min_length=1)
    actor_type: str | None = Field(default=None, max_length=32)
    actor_ref: str | None = Field(default=None, max_length=255)
    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    reason_code: str = Field(default="review_changes_requested", min_length=1, max_length=64)


class TaskReviewRequestChangesTool(ToolBase):
    """Return one review task back for changes."""

    name = "task.review.request_changes"
    description = "Request changes for one Task Flow task currently in review."
    parameters_model = TaskReviewRequestChangesParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskReviewRequestChangesParams)
            else TaskReviewRequestChangesParams.model_validate(params)
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
            item = await service.request_review_changes(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                reason_text=payload.reason_text,
                actor_type="ai_profile",
                actor_ref=ctx.profile_id,
                actor_session_id=ctx.session_id,
                owner_type=payload.owner_type,
                owner_ref=payload.owner_ref,
                reason_code=payload.reason_code,
            )
            return ToolResult(ok=True, payload={"task": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.review.request_changes tool instance."""

    return TaskReviewRequestChangesTool(settings=settings)
