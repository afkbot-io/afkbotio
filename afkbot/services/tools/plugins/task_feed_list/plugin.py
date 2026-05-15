"""Tool plugin for task.feed.list."""

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


class TaskFeedListParams(ToolParameters):
    """Parameters for task.feed.list tool."""

    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    owner_profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    owner_subagent_name: str | None = Field(default=None, min_length=1, max_length=255)
    task_limit: int = Field(default=10, ge=1, le=50)
    event_limit: int = Field(default=10, ge=1, le=50)


class TaskFeedListTool(ToolBase):
    """Return an AI Task Flow feed for assignments and lifecycle signals."""

    name = "task.feed.list"
    description = (
        "List AI Task Flow assignments plus explicit mention, wake, recovery, and reject events."
    )
    parameters_model = TaskFeedListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskFeedListParams)
            else TaskFeedListParams.model_validate(params)
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
            owner_type, owner_ref = resolve_task_owner_inputs(
                field_prefix="owner",
                owner_type=payload.owner_type,
                owner_ref=payload.owner_ref,
                owner_profile_id=payload.owner_profile_id,
                owner_subagent_name=payload.owner_subagent_name,
            )
            if owner_type is None and owner_ref is None:
                owner_type = "ai_profile"
                owner_ref = ctx.profile_id
            feed = await service.build_agent_inbox(
                profile_id=target_profile_id,
                owner_type=owner_type or "",
                owner_ref=owner_ref or "",
                task_limit=payload.task_limit,
                event_limit=payload.event_limit,
            )
            return ToolResult(ok=True, payload={"feed": feed.model_dump(mode="json")})
        except TaskOwnerInputError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.feed.list tool instance."""

    return TaskFeedListTool(settings=settings)
