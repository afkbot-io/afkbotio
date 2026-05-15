"""Tool plugin for task.doc.put."""

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


class TaskDocPutParams(ToolParameters):
    """Parameters for task.doc.put tool."""

    scope_type: str = Field(min_length=1, max_length=16)
    scope_id: str = Field(min_length=1, max_length=64)
    document_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1)
    base_revision: int | None = Field(default=None, ge=1)


class TaskDocPutTool(ToolBase):
    """Create or update one Task Flow document revision."""

    name = "task.doc.put"
    description = "Create or update one Task Flow flow/task document revision."
    parameters_model = TaskDocPutParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskDocPutParams)
            else TaskDocPutParams.model_validate(params)
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
            scope_type = payload.scope_type.strip().lower()
            if scope_type == "flow":
                document = await service.put_flow_document(
                    profile_id=target_profile_id,
                    flow_id=payload.scope_id,
                    document_key=payload.document_key,
                    title=payload.title,
                    body=payload.body,
                    actor_type=actor.actor_type,
                    actor_ref=actor.actor_ref,
                    actor_session_id=actor.actor_session_id,
                    base_revision=payload.base_revision,
                )
            elif scope_type == "task":
                document = await service.put_task_document(
                    profile_id=target_profile_id,
                    task_id=payload.scope_id,
                    document_key=payload.document_key,
                    title=payload.title,
                    body=payload.body,
                    actor_type=actor.actor_type,
                    actor_ref=actor.actor_ref,
                    actor_session_id=actor.actor_session_id,
                    base_revision=payload.base_revision,
                )
            else:
                return ToolResult.error(
                    error_code="invalid_task_document_scope",
                    reason="scope_type must be flow or task",
                )
            return ToolResult(ok=True, payload={"document": document.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.doc.put tool instance."""

    return TaskDocPutTool(settings=settings)
