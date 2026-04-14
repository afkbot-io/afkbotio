"""Tool plugin for task.create."""

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


class TaskCreateParams(ToolParameters):
    """Parameters for task.create tool."""

    title: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=1)
    flow_id: str | None = Field(default=None, max_length=64)
    priority: int = Field(default=50, ge=0)
    due_at: datetime | None = None
    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    reviewer_type: str | None = Field(default=None, max_length=32)
    reviewer_ref: str | None = Field(default=None, max_length=255)
    source_type: str = Field(default="manual", max_length=64)
    source_ref: str | None = Field(default=None, max_length=255)
    labels: tuple[str, ...] = ()
    requires_review: bool = False
    depends_on_task_ids: tuple[str, ...] = ()


class TaskCreateTool(ToolBase):
    """Create one task for the active profile."""

    name = "task.create"
    description = "Create one Task Flow task."
    parameters_model = TaskCreateParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskCreateParams) else TaskCreateParams.model_validate(params)
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
            item = await service.create_task(
                profile_id=target_profile_id,
                title=payload.title,
                prompt=payload.prompt,
                created_by_type="ai_profile",
                created_by_ref=ctx.profile_id,
                actor_session_id=ctx.session_id,
                flow_id=payload.flow_id,
                priority=payload.priority,
                due_at=payload.due_at,
                owner_type=payload.owner_type,
                owner_ref=payload.owner_ref,
                reviewer_type=payload.reviewer_type,
                reviewer_ref=payload.reviewer_ref,
                source_type=payload.source_type,
                source_ref=payload.source_ref,
                labels=payload.labels,
                requires_review=payload.requires_review,
                depends_on_task_ids=payload.depends_on_task_ids,
            )
            return ToolResult(ok=True, payload={"task": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.create tool instance."""

    return TaskCreateTool(settings=settings)
