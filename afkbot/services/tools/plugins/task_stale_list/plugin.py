"""Tool plugin for task.stale.list."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.task_flow.owner_inputs import TaskOwnerInputError, resolve_task_owner_inputs
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskStaleListParams(ToolParameters):
    """Parameters for task.stale.list tool."""

    limit: int | None = Field(default=None, ge=1, le=100)
    owner_ref: str | None = Field(default=None, min_length=1, max_length=255)
    owner_profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    owner_subagent_name: str | None = Field(default=None, min_length=1, max_length=255)


class TaskStaleListTool(ToolBase):
    """List stale claimed/running Task Flow tasks for one profile."""

    name = "task.stale.list"
    description = "List stale claimed/running Task Flow tasks whose lease already expired."
    parameters_model = TaskStaleListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskStaleListParams) else TaskStaleListParams.model_validate(params)
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
            _, resolved_owner_ref = resolve_task_owner_inputs(
                field_prefix="owner",
                owner_type=None,
                owner_ref=payload.owner_ref,
                owner_profile_id=payload.owner_profile_id,
                owner_subagent_name=payload.owner_subagent_name,
            )
            items = await service.list_stale_task_claims(
                profile_id=target_profile_id,
                owner_ref=resolved_owner_ref,
                limit=payload.limit,
            )
            return ToolResult(
                ok=True,
                payload={"stale_task_claims": [item.model_dump(mode="json") for item in items]},
            )
        except TaskOwnerInputError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.stale.list tool instance."""

    return TaskStaleListTool(settings=settings)
