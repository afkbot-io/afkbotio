"""Tool plugin for task.list."""

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


class TaskListParams(ToolParameters):
    """Parameters for task.list tool."""

    statuses: tuple[str, ...] = ()
    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    flow_id: str | None = Field(default=None, max_length=64)
    limit: int | None = Field(default=None, ge=1, le=100)


class TaskListTool(ToolBase):
    """List tasks for the active profile."""

    name = "task.list"
    description = "List Task Flow tasks."
    parameters_model = TaskListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskListParams) else TaskListParams.model_validate(params)
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
            items = await service.list_tasks(
                profile_id=target_profile_id,
                statuses=payload.statuses,
                owner_type=payload.owner_type,
                owner_ref=payload.owner_ref,
                flow_id=payload.flow_id,
                limit=payload.limit,
            )
            return ToolResult(ok=True, payload={"tasks": [item.model_dump(mode="json") for item in items]})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.list tool instance."""

    return TaskListTool(settings=settings)
