"""Tool plugin for task.review.list."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.task_flow.owner_inputs import TaskOwnerInputError, resolve_task_owner_inputs
from afkbot.services.task_flow_principals import EMPLOYEE_OWNER_TYPE
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import (
    ensure_employee_task_read_scope,
    resolve_task_tool_actor,
    restrict_employee_read_owner_scope,
)
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskReviewListParams(ToolParameters):
    """Parameters for task.review.list tool."""

    all_reviewers: bool = False
    actor_type: str | None = Field(default=None, max_length=32)
    actor_ref: str | None = Field(default=None, min_length=1, max_length=255)
    flow_id: str | None = Field(default=None, max_length=64)
    labels: tuple[str, ...] = ()
    limit: int | None = Field(default=None, ge=1, le=100)


class TaskReviewListTool(ToolBase):
    """List review queue tasks for one reviewer actor."""

    name = "task.review.list"
    description = "List Task Flow review queue tasks for one actor."
    parameters_model = TaskReviewListParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskReviewListParams)
            else TaskReviewListParams.model_validate(params)
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
            runtime_actor = resolve_task_tool_actor(ctx)
            employee_runtime = runtime_actor.actor_type == EMPLOYEE_OWNER_TYPE
            if payload.all_reviewers:
                if any(
                    (
                        payload.actor_type,
                        payload.actor_ref,
                    )
                ):
                    return ToolResult.error(
                        error_code="invalid_actor",
                        reason="all_reviewers cannot be combined with actor selectors",
                    )
                resolved_actor_type, resolved_actor_ref = None, None
            else:
                resolved_actor_type, resolved_actor_ref = resolve_task_owner_inputs(
                    field_prefix="actor",
                    owner_type=payload.actor_type,
                    owner_ref=payload.actor_ref,
                )
                if resolved_actor_type is None or resolved_actor_ref is None:
                    if employee_runtime:
                        resolved_actor_type = EMPLOYEE_OWNER_TYPE
                        resolved_actor_ref = runtime_actor.actor_ref
                    else:
                        return ToolResult.error(
                            error_code="invalid_actor",
                            reason="actor_type and actor_ref must be provided together",
                        )
                if employee_runtime:
                    resolved_actor_type, resolved_actor_ref, read_scope_error = (
                        await restrict_employee_read_owner_scope(
                            ctx=ctx,
                            settings=self._settings,
                            target_profile_id=target_profile_id,
                            owner_type=resolved_actor_type,
                            owner_ref=resolved_actor_ref,
                        )
                    )
                    if read_scope_error is not None:
                        return read_scope_error
            items = await service.list_review_tasks(
                profile_id=target_profile_id,
                actor_type=resolved_actor_type,
                actor_ref=resolved_actor_ref,
                flow_id=payload.flow_id,
                labels=payload.labels,
                limit=payload.limit,
            )
            if employee_runtime and payload.all_reviewers:
                visible_items = []
                for item in items:
                    read_error = await ensure_employee_task_read_scope(
                        ctx=ctx,
                        settings=self._settings,
                        target_profile_id=target_profile_id,
                        task=item,
                    )
                    if read_error is None:
                        visible_items.append(item)
                        continue
                    if read_error.error_code != "task_employee_scope_forbidden":
                        return read_error
                items = visible_items
            review_scope: dict[str, object] = (
                (
                    {
                        "kind": "employee_visible_reviewers",
                        "actor_type": EMPLOYEE_OWNER_TYPE,
                        "actor_ref": runtime_actor.actor_ref,
                    }
                    if employee_runtime
                    else {"kind": "all_reviewers"}
                )
                if payload.all_reviewers
                else {
                    "kind": "actor",
                    "actor_type": resolved_actor_type,
                    "actor_ref": resolved_actor_ref,
                }
            )
            return ToolResult(
                ok=True,
                payload={
                    "review_tasks": [item.model_dump(mode="json") for item in items],
                    "review_scope": review_scope,
                },
            )
        except TaskOwnerInputError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.review.list tool instance."""

    return TaskReviewListTool(settings=settings)
