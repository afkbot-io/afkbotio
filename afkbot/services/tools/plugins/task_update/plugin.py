"""Tool plugin for task.update."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskUpdateParams(ToolParameters):
    """Parameters for task.update tool."""

    task_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    prompt: str | None = Field(default=None, min_length=1)
    status: str | None = Field(default=None, max_length=32)
    priority: int | None = Field(default=None, ge=0)
    due_at: datetime | None = None
    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    reviewer_type: str | None = Field(default=None, max_length=32)
    reviewer_ref: str | None = Field(default=None, max_length=255)
    requires_review: bool | None = None
    labels: tuple[str, ...] | None = None
    blocked_reason_code: str | None = Field(default=None, max_length=64)
    blocked_reason_text: str | None = None


class TaskUpdateTool(ToolBase):
    """Update one task for the active profile."""

    name = "task.update"
    description = "Update one Task Flow task."
    parameters_model = TaskUpdateParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskUpdateParams) else TaskUpdateParams.model_validate(params)
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
            item = await service.update_task(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                title=payload.title,
                prompt=payload.prompt,
                status=payload.status,
                priority=payload.priority,
                due_at=payload.due_at,
                owner_type=payload.owner_type,
                owner_ref=payload.owner_ref,
                reviewer_type=payload.reviewer_type,
                reviewer_ref=payload.reviewer_ref,
                requires_review=payload.requires_review,
                labels=payload.labels,
                blocked_reason_code=payload.blocked_reason_code,
                blocked_reason_text=payload.blocked_reason_text,
                actor_type="ai_profile",
                actor_ref=ctx.profile_id,
            )
            return ToolResult(ok=True, payload={"task": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.update tool instance."""

    return TaskUpdateTool(settings=settings)
