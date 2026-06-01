"""Tool plugin for task.doc.list."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import (
    ensure_employee_flow_read_scope,
    ensure_employee_task_read_scope,
)
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskDocListParams(ToolParameters):
    """Parameters for task.doc.list tool."""

    scope_type: str = Field(min_length=1, max_length=16)
    scope_id: str = Field(min_length=1, max_length=64)


class TaskDocListTool(ToolBase):
    """List Task Flow documents for a flow or task."""

    name = "task.doc.list"
    description = "List editable Task Flow documents for one flow or task."
    parameters_model = TaskDocListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskDocListParams)
            else TaskDocListParams.model_validate(params)
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
            scope_type = payload.scope_type.strip().lower()
            if scope_type == "flow":
                flow = await service.get_flow(
                    profile_id=target_profile_id,
                    flow_id=payload.scope_id,
                )
                read_error = await ensure_employee_flow_read_scope(
                    ctx=ctx,
                    settings=self._settings,
                    target_profile_id=target_profile_id,
                    flow=flow,
                )
                if read_error is not None:
                    return read_error
                documents = await service.list_flow_documents(
                    profile_id=target_profile_id,
                    flow_id=payload.scope_id,
                )
            elif scope_type == "task":
                task = await service.get_task(profile_id=target_profile_id, task_id=payload.scope_id)
                read_error = await ensure_employee_task_read_scope(
                    ctx=ctx,
                    settings=self._settings,
                    target_profile_id=target_profile_id,
                    task=task,
                )
                if read_error is not None:
                    return read_error
                context = await service.build_task_context(
                    profile_id=target_profile_id,
                    task_id=payload.scope_id,
                    event_limit=1,
                    comment_limit=1,
                )
                documents = list(context.task_documents)
            else:
                return ToolResult.error(
                    error_code="invalid_task_document_scope",
                    reason="scope_type must be flow or task",
                )
            return ToolResult(
                ok=True,
                payload={"documents": [item.model_dump(mode="json") for item in documents]},
            )
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.doc.list tool instance."""

    return TaskDocListTool(settings=settings)
