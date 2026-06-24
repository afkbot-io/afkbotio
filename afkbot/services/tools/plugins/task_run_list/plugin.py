"""Tool plugin for task.run.list."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.task_flow_principals import EMPLOYEE_OWNER_TYPE
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import (
    ensure_employee_task_read_scope,
    resolve_task_tool_actor,
)
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskRunListParams(ToolParameters):
    """Parameters for task.run.list tool."""

    task_id: str | None = Field(default=None, max_length=64)
    limit: int | None = Field(default=None, ge=1, le=100)


class TaskRunListTool(ToolBase):
    """List task execution attempts."""

    name = "task.run.list"
    description = "List Task Flow execution attempts."
    parameters_model = TaskRunListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskRunListParams)
            else TaskRunListParams.model_validate(params)
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
            if resolve_task_tool_actor(ctx).actor_type == EMPLOYEE_OWNER_TYPE:
                if payload.task_id is None:
                    return ToolResult.error(
                        error_code="task_employee_scope_forbidden",
                        reason="Employee runtimes must list task runs through a readable task_id.",
                    )
                task = await service.get_task(
                    profile_id=target_profile_id,
                    task_id=payload.task_id,
                )
                read_error = await ensure_employee_task_read_scope(
                    ctx=ctx,
                    settings=self._settings,
                    target_profile_id=target_profile_id,
                    task=task,
                )
                if read_error is not None:
                    return read_error
            items = await service.list_task_runs(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                limit=payload.limit,
            )
            return ToolResult(
                ok=True, payload={"task_runs": [item.model_dump(mode="json") for item in items]}
            )
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.run.list tool instance."""

    return TaskRunListTool(settings=settings)
