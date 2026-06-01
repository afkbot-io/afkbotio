"""Tool plugin for task.update."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from afkbot.services.task_flow import (
    TASK_FLOW_FIELD_UNSET,
    TaskAttachmentCreate,
    TaskFlowServiceError,
    get_task_flow_service,
)
from afkbot.services.task_flow.owner_inputs import TaskOwnerInputError, resolve_task_owner_inputs
from afkbot.services.task_flow.timing import TaskTimingInputError, resolve_ready_at_update
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import resolve_task_tool_actor
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskUpdateParams(ToolParameters):
    """Parameters for task.update tool."""

    task_id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, min_length=1)
    status: str | None = Field(default=None, max_length=32)
    priority: int | None = Field(default=None, ge=0)
    due_at: datetime | None = None
    ready_at: datetime | None = None
    retry_after_sec: int | None = Field(default=None, ge=1)
    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    reviewer_type: str | None = Field(default=None, max_length=32)
    reviewer_ref: str | None = Field(default=None, max_length=255)
    requires_review: bool | None = None
    labels: tuple[str, ...] | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    session_profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    blocked_reason_code: str | None = Field(default=None, max_length=64)
    blocked_reason_text: str | None = None
    attachments: tuple[TaskAttachmentCreate, ...] = ()


class TaskUpdateTool(ToolBase):
    """Update one task for the active profile."""

    name = "task.update"
    description = "Update one Task Flow task."
    parameters_model = TaskUpdateParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskUpdateParams)
            else TaskUpdateParams.model_validate(params)
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
                field_prefix="owner",
                owner_type=payload.owner_type,
                owner_ref=payload.owner_ref,
            )
            explicit_fields = set(getattr(payload, "model_fields_set", set()))
            reviewer_fields_explicit = bool(
                explicit_fields
                & {
                    "reviewer_type",
                    "reviewer_ref",
                }
            )
            if reviewer_fields_explicit:
                resolved_reviewer_type: str | None | object
                resolved_reviewer_ref: str | None | object
                resolved_reviewer_type, resolved_reviewer_ref = resolve_task_owner_inputs(
                    field_prefix="reviewer",
                    owner_type=payload.reviewer_type,
                    owner_ref=payload.reviewer_ref,
                )
            else:
                resolved_reviewer_type = TASK_FLOW_FIELD_UNSET
                resolved_reviewer_ref = TASK_FLOW_FIELD_UNSET
            session_id_explicit = "session_id" in explicit_fields
            session_profile_id_explicit = "session_profile_id" in explicit_fields
            if actor.actor_type == "automation" and (
                session_id_explicit or session_profile_id_explicit
            ):
                return ToolResult.error(
                    error_code="task_session_binding_forbidden",
                    reason="automation graph runtime does not support explicit task session bindings",
                )
            effective_session_id = payload.session_id if session_id_explicit else None
            effective_session_profile_id = (
                payload.session_profile_id if session_profile_id_explicit else None
            )
            blocked_reason_code_explicit = "blocked_reason_code" in explicit_fields
            blocked_reason_text_explicit = "blocked_reason_text" in explicit_fields
            blocked_reason_code_arg = (
                payload.blocked_reason_code
                if blocked_reason_code_explicit
                else TASK_FLOW_FIELD_UNSET
            )
            blocked_reason_text_arg = (
                payload.blocked_reason_text
                if blocked_reason_text_explicit
                else TASK_FLOW_FIELD_UNSET
            )
            try:
                ready_at_update = resolve_ready_at_update(
                    explicit_fields=explicit_fields,
                    ready_at=payload.ready_at,
                    retry_after_sec=payload.retry_after_sec,
                    status=payload.status,
                    require_blocked_status_for_retry=True,
                )
            except TaskTimingInputError as exc:
                return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
            if (
                actor.actor_type != "automation"
                and effective_session_id is None
                and payload.status in {"claimed", "running"}
            ):
                effective_session_id = ctx.session_id
                if not session_profile_id_explicit:
                    effective_session_profile_id = ctx.profile_id
            elif (
                actor.actor_type != "automation"
                and effective_session_id is not None
                and effective_session_id == ctx.session_id
                and effective_session_profile_id is None
                and not session_profile_id_explicit
            ):
                effective_session_profile_id = ctx.profile_id
            session_id_arg: str | None | object = TASK_FLOW_FIELD_UNSET
            session_profile_id_arg: str | None | object = TASK_FLOW_FIELD_UNSET
            if session_id_explicit or effective_session_id is not None:
                session_id_arg = effective_session_id
                session_profile_id_arg = (
                    effective_session_profile_id
                    if session_profile_id_explicit or effective_session_profile_id is not None
                    else None
                )
            item = await service.update_task(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                title=payload.title,
                description=payload.description,
                status=payload.status,
                priority=payload.priority,
                due_at=payload.due_at,
                ready_at=(
                    ready_at_update.ready_at
                    if ready_at_update.should_update
                    else TASK_FLOW_FIELD_UNSET
                ),
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=resolved_reviewer_type,
                reviewer_ref=resolved_reviewer_ref,
                requires_review=payload.requires_review,
                labels=payload.labels,
                session_id=session_id_arg,
                session_profile_id=session_profile_id_arg,
                blocked_reason_code=blocked_reason_code_arg,
                blocked_reason_text=blocked_reason_text_arg,
                actor_session_id=actor.actor_session_id,
                actor_type=actor.actor_type,
                actor_ref=actor.actor_ref,
                attachments=payload.attachments,
            )
            return ToolResult(ok=True, payload={"task": item.model_dump(mode="json")})
        except TaskOwnerInputError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.update tool instance."""

    return TaskUpdateTool(settings=settings)
