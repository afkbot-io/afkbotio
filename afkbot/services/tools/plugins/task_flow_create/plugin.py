"""Tool plugin for task.flow.create."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.task_flow.owner_inputs import TaskOwnerInputError, resolve_task_owner_inputs
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import resolve_task_tool_actor
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskFlowCreateParams(ToolParameters):
    """Parameters for task.flow.create tool."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    default_owner_type: str | None = Field(default=None, max_length=32)
    default_owner_ref: str | None = Field(default=None, max_length=255)
    default_owner_profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    default_owner_subagent_name: str | None = Field(default=None, min_length=1, max_length=255)
    labels: tuple[str, ...] = ()


class TaskFlowCreateTool(ToolBase):
    """Create one Task Flow container."""

    name = "task.flow.create"
    description = "Create one Task Flow container."
    parameters_model = TaskFlowCreateParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskFlowCreateParams)
            else TaskFlowCreateParams.model_validate(params)
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
            actor = resolve_task_tool_actor(ctx)
            resolved_owner_type, resolved_owner_ref = resolve_task_owner_inputs(
                field_prefix="default_owner",
                owner_type=payload.default_owner_type,
                owner_ref=payload.default_owner_ref,
                owner_profile_id=payload.default_owner_profile_id,
                owner_subagent_name=payload.default_owner_subagent_name,
            )
            item = await service.create_flow(
                profile_id=target_profile_id,
                title=payload.title,
                description=payload.description,
                created_by_type=actor.actor_type,
                created_by_ref=actor.actor_ref,
                actor_session_id=actor.actor_session_id,
                default_owner_type=resolved_owner_type,
                default_owner_ref=resolved_owner_ref,
                labels=payload.labels,
            )
            return ToolResult(ok=True, payload={"task_flow": item.model_dump(mode="json")})
        except TaskOwnerInputError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.flow.create tool instance."""

    return TaskFlowCreateTool(settings=settings)
