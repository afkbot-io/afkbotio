"""Tool plugin for task.board."""

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


class TaskBoardParams(ToolParameters):
    """Parameters for task.board tool."""

    flow_id: str | None = Field(default=None, max_length=64)
    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    labels: tuple[str, ...] = ()
    limit_per_column: int = Field(default=20, ge=1, le=100)


class TaskBoardTool(ToolBase):
    """Build one read-only board projection for Task Flow backlog."""

    name = "task.board"
    description = "Build one Task Flow board projection."
    parameters_model = TaskBoardParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskBoardParams) else TaskBoardParams.model_validate(params)
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
            board = await service.build_board(
                profile_id=target_profile_id,
                flow_id=payload.flow_id,
                owner_type=payload.owner_type,
                owner_ref=payload.owner_ref,
                labels=payload.labels,
                limit_per_column=payload.limit_per_column,
            )
            return ToolResult(ok=True, payload={"board": board.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.board tool instance."""

    return TaskBoardTool(settings=settings)
