"""Tool plugin for task.delegate."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
    runtime_task_id,
)
from afkbot.settings import Settings


class TaskDelegateParams(ToolParameters):
    """Parameters for task.delegate tool."""

    task_id: str | None = Field(default=None, min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str = Field(min_length=1)
    owner_ref: str = Field(min_length=1, max_length=255)
    flow_id: str | None = Field(default=None, max_length=64)
    priority: int | None = Field(default=None, ge=0)
    due_at: datetime | None = None
    labels: tuple[str, ...] | None = None
    requires_review: bool | None = None
    wait_for_delegated_task: bool = True
    handoff_note: str | None = None


class TaskDelegateTool(ToolBase):
    """Delegate one task to another AI-owned task."""

    name = "task.delegate"
    description = "Delegate work from one task to another ai_profile task."
    parameters_model = TaskDelegateParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskDelegateParams) else TaskDelegateParams.model_validate(params)
        explicit_fields = set(getattr(payload, "model_fields_set", set()))
        target_profile_id = resolve_task_target_profile(
            ctx=ctx,
            payload=payload,
            explicit_fields=explicit_fields,
        )
        scope_error = ensure_task_target_scope(ctx=ctx, target_profile_id=target_profile_id)
        if scope_error is not None:
            return scope_error
        source_task_id = payload.task_id if "task_id" in explicit_fields else None
        if source_task_id is None:
            source_task_id = runtime_task_id(ctx=ctx)
        if source_task_id is None:
            return ToolResult.error(error_code="invalid_task_id", reason="task_id is required")
        try:
            service = get_task_flow_service(self._settings)
            item = await service.delegate_task(
                profile_id=target_profile_id,
                source_task_id=source_task_id,
                delegated_owner_ref=payload.owner_ref,
                description=payload.description,
                actor_type="ai_profile",
                actor_ref=ctx.profile_id,
                actor_session_id=ctx.session_id,
                title=payload.title,
                flow_id=payload.flow_id,
                priority=payload.priority,
                due_at=payload.due_at,
                labels=payload.labels,
                requires_review=payload.requires_review,
                wait_for_delegated_task=payload.wait_for_delegated_task,
                handoff_note=payload.handoff_note,
            )
            return ToolResult(ok=True, payload={"delegation": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.delegate tool instance."""

    return TaskDelegateTool(settings=settings)
