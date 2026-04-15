"""Tool plugin for task.block."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


class TaskBlockParams(ToolParameters):
    """Parameters for task.block tool."""

    task_id: str | None = Field(default=None, min_length=1, max_length=64)
    reason_code: str = Field(min_length=1, max_length=64)
    reason_text: str = Field(min_length=1)
    ready_at: datetime | None = None
    retry_after_sec: int | None = Field(default=None, ge=1)
    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    reviewer_type: str | None = Field(default=None, max_length=32)
    reviewer_ref: str | None = Field(default=None, max_length=255)


class TaskBlockTool(ToolBase):
    """Block one task with an explicit reason."""

    name = "task.block"
    description = "Block one Task Flow task with an explicit reason."
    parameters_model = TaskBlockParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskBlockParams) else TaskBlockParams.model_validate(params)
        explicit_fields = set(getattr(payload, "model_fields_set", set()))
        target_profile_id = resolve_task_target_profile(
            ctx=ctx,
            payload=payload,
            explicit_fields=explicit_fields,
        )
        scope_error = ensure_task_target_scope(ctx=ctx, target_profile_id=target_profile_id)
        if scope_error is not None:
            return scope_error
        task_id = payload.task_id if "task_id" in explicit_fields else None
        if task_id is None:
            task_id = runtime_task_id(ctx=ctx)
        if task_id is None:
            return ToolResult.error(error_code="invalid_task_id", reason="task_id is required")
        ready_at_explicit = "ready_at" in explicit_fields
        retry_after_explicit = "retry_after_sec" in explicit_fields
        if ready_at_explicit and retry_after_explicit:
            return ToolResult.error(
                error_code="task_ready_at_conflict",
                reason="ready_at and retry_after_sec cannot be used together",
            )
        effective_ready_at: datetime | None = None
        if ready_at_explicit:
            effective_ready_at = payload.ready_at
        elif retry_after_explicit:
            effective_ready_at = datetime.now(timezone.utc) + timedelta(
                seconds=payload.retry_after_sec or 0
            )
        try:
            service = get_task_flow_service(self._settings)
            if ready_at_explicit or retry_after_explicit:
                item = await service.block_task(
                    profile_id=target_profile_id,
                    task_id=task_id,
                    reason_code=payload.reason_code,
                    reason_text=payload.reason_text,
                    actor_type="ai_profile",
                    actor_ref=ctx.profile_id,
                    actor_session_id=ctx.session_id,
                    ready_at=effective_ready_at,
                    owner_type=payload.owner_type,
                    owner_ref=payload.owner_ref,
                    reviewer_type=payload.reviewer_type,
                    reviewer_ref=payload.reviewer_ref,
                )
            else:
                item = await service.block_task(
                    profile_id=target_profile_id,
                    task_id=task_id,
                    reason_code=payload.reason_code,
                    reason_text=payload.reason_text,
                    actor_type="ai_profile",
                    actor_ref=ctx.profile_id,
                    actor_session_id=ctx.session_id,
                    owner_type=payload.owner_type,
                    owner_ref=payload.owner_ref,
                    reviewer_type=payload.reviewer_type,
                    reviewer_ref=payload.reviewer_ref,
                )
            return ToolResult(ok=True, payload={"task": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.block tool instance."""

    return TaskBlockTool(settings=settings)
