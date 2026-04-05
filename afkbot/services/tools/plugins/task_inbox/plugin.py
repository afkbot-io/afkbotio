"""Tool plugin for task.inbox."""

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


class TaskInboxParams(ToolParameters):
    """Parameters for task.inbox tool."""

    owner_ref: str = Field(min_length=1, max_length=255)
    task_limit: int = Field(default=5, ge=1, le=50)
    event_limit: int = Field(default=5, ge=1, le=50)
    channel: str | None = Field(default=None, max_length=64)
    mark_seen: bool = False


class TaskInboxTool(ToolBase):
    """Build one notification-ready human inbox summary."""

    name = "task.inbox"
    description = "Build one notification-ready Task Flow human inbox summary."
    parameters_model = TaskInboxParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskInboxParams) else TaskInboxParams.model_validate(params)
        target_profile_id = resolve_task_target_profile(
            ctx=ctx,
            payload=payload,
            explicit_fields=set(getattr(payload, "model_fields_set", set())),
        )
        scope_error = ensure_task_target_scope(ctx=ctx, target_profile_id=target_profile_id)
        if scope_error is not None:
            return scope_error
        if payload.mark_seen:
            return ToolResult.error(
                error_code="task_inbox_mark_seen_not_allowed",
                reason="task.inbox cannot advance seen cursors from tool execution",
            )
        try:
            service = get_task_flow_service(self._settings)
            inbox = await service.build_human_inbox(
                profile_id=target_profile_id,
                owner_ref=payload.owner_ref,
                task_limit=payload.task_limit,
                event_limit=payload.event_limit,
                channel=payload.channel,
                mark_seen=payload.mark_seen,
            )
            return ToolResult(ok=True, payload={"inbox": inbox.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.inbox tool instance."""

    return TaskInboxTool(settings=settings)
