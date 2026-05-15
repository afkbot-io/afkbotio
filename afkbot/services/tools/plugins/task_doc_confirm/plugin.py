"""Tool plugin for task.doc.confirm."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import resolve_task_tool_actor
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskDocConfirmParams(ToolParameters):
    """Parameters for task.doc.confirm tool."""

    document_id: str = Field(min_length=1, max_length=64)
    expected_revision: int | None = Field(default=None, ge=1)


class TaskDocConfirmTool(ToolBase):
    """Confirm the current revision of one Task Flow document."""

    name = "task.doc.confirm"
    description = "Confirm the current revision of one Task Flow document."
    parameters_model = TaskDocConfirmParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskDocConfirmParams)
            else TaskDocConfirmParams.model_validate(params)
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
            document = await service.confirm_document(
                profile_id=target_profile_id,
                document_id=payload.document_id,
                actor_type=actor.actor_type,
                actor_ref=actor.actor_ref,
                actor_session_id=actor.actor_session_id,
                expected_revision=payload.expected_revision,
            )
            return ToolResult(ok=True, payload={"document": document.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.doc.confirm tool instance."""

    return TaskDocConfirmTool(settings=settings)
