"""Tool plugin for task.flow.list."""

from __future__ import annotations

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskFlowListParams(ToolParameters):
    """Parameters for task.flow.list tool."""


class TaskFlowListTool(ToolBase):
    """List Task Flow containers."""

    name = "task.flow.list"
    description = "List Task Flow containers."
    parameters_model = TaskFlowListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskFlowListParams)
            else TaskFlowListParams.model_validate(params)
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
            items = await service.list_flows(profile_id=target_profile_id)
            return ToolResult(
                ok=True,
                payload={"task_flows": [item.model_dump(mode="json") for item in items]},
            )
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.flow.list tool instance."""

    return TaskFlowListTool(settings=settings)
