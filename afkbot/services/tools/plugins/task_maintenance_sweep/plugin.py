"""Tool plugin for task.stale.sweep."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field

from afkbot.services.task_flow import (
    TaskFlowServiceError,
    TaskMaintenanceSweepMetadata,
    get_task_flow_service,
)
from afkbot.services.task_flow.runtime_service import TaskFlowRuntimeService
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskMaintenanceSweepParams(ToolParameters):
    """Parameters for task.stale.sweep tool."""

    limit: int | None = Field(default=None, ge=1, le=100)


class TaskMaintenanceSweepTool(ToolBase):
    """Force one stale-claim maintenance sweep inside the selected Task Flow profile."""

    name = "task.stale.sweep"
    description = "Force one stale-claim maintenance sweep for the selected Task Flow profile."
    parameters_model = TaskMaintenanceSweepParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskMaintenanceSweepParams)
            else TaskMaintenanceSweepParams.model_validate(params)
        )
        target_profile_id = resolve_task_target_profile(
            ctx=ctx,
            payload=payload,
            explicit_fields=set(getattr(payload, "model_fields_set", set())),
        )
        scope_error = ensure_task_target_scope(ctx=ctx, target_profile_id=target_profile_id)
        if scope_error is not None:
            return scope_error
        runtime = TaskFlowRuntimeService(settings=self._settings)
        limit = (
            payload.limit
            if payload.limit is not None
            else self._settings.taskflow_runtime_maintenance_batch_size
        )
        try:
            released_count = await runtime.sweep_expired_claims(
                worker_id=f"taskflow-tool-maintenance:{ctx.profile_id}",
                profile_id=target_profile_id,
                limit=limit,
            )
            service = get_task_flow_service(self._settings)
            remaining = await service.list_stale_task_claims(
                profile_id=target_profile_id,
                limit=limit,
            )
            metadata = TaskMaintenanceSweepMetadata(
                generated_at=datetime.now(timezone.utc),
                profile_id=target_profile_id,
                limit=limit,
                repaired_count=released_count,
                remaining_count=len(remaining),
                remaining=remaining,
            )
            return ToolResult(
                ok=True,
                payload={"maintenance": metadata.model_dump(mode="json")},
            )
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        finally:
            await runtime.shutdown()


def create_tool(settings: Settings) -> ToolBase:
    """Create task.stale.sweep tool instance."""

    return TaskMaintenanceSweepTool(settings=settings)
