"""Tool plugin for task.update."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import Field

from afkbot.services.task_flow import (
    TASK_FLOW_FIELD_UNSET,
    TaskAttachmentCreate,
    TaskFlowServiceError,
    get_task_flow_service,
)
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
        payload = params if isinstance(params, TaskUpdateParams) else TaskUpdateParams.model_validate(params)
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
            explicit_fields = set(getattr(payload, "model_fields_set", set()))
            session_id_explicit = "session_id" in explicit_fields
            session_profile_id_explicit = "session_profile_id" in explicit_fields
            if actor.actor_type == "automation" and (session_id_explicit or session_profile_id_explicit):
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
                payload.blocked_reason_code if blocked_reason_code_explicit else TASK_FLOW_FIELD_UNSET
            )
            blocked_reason_text_arg = (
                payload.blocked_reason_text if blocked_reason_text_explicit else TASK_FLOW_FIELD_UNSET
            )
            ready_at_explicit = "ready_at" in explicit_fields
            retry_after_explicit = "retry_after_sec" in explicit_fields
            if ready_at_explicit and retry_after_explicit:
                return ToolResult.error(
                    error_code="task_ready_at_conflict",
                    reason="ready_at and retry_after_sec cannot be used together",
                )
            effective_ready_at = payload.ready_at
            if retry_after_explicit:
                if payload.status != "blocked":
                    return ToolResult.error(
                        error_code="task_retry_after_requires_blocked_status",
                        reason="retry_after_sec requires status=blocked",
                    )
                effective_ready_at = datetime.now(timezone.utc) + timedelta(seconds=payload.retry_after_sec or 0)
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
            if ready_at_explicit or retry_after_explicit:
                if effective_session_id is not None:
                    item = await service.update_task(
                        profile_id=target_profile_id,
                        task_id=payload.task_id,
                        title=payload.title,
                        description=payload.description,
                        status=payload.status,
                        priority=payload.priority,
                        due_at=payload.due_at,
                        ready_at=effective_ready_at,
                        owner_type=payload.owner_type,
                        owner_ref=payload.owner_ref,
                        reviewer_type=payload.reviewer_type,
                        reviewer_ref=payload.reviewer_ref,
                        requires_review=payload.requires_review,
                        labels=payload.labels,
                        session_id=effective_session_id,
                        session_profile_id=(
                            effective_session_profile_id
                            if session_profile_id_explicit or effective_session_profile_id is not None
                            else None
                        ),
                        blocked_reason_code=blocked_reason_code_arg,
                        blocked_reason_text=blocked_reason_text_arg,
                        actor_session_id=actor.actor_session_id,
                        actor_type=actor.actor_type,
                        actor_ref=actor.actor_ref,
                        attachments=payload.attachments,
                    )
                else:
                    item = await service.update_task(
                        profile_id=target_profile_id,
                        task_id=payload.task_id,
                        title=payload.title,
                        description=payload.description,
                        status=payload.status,
                        priority=payload.priority,
                        due_at=payload.due_at,
                        ready_at=effective_ready_at,
                        owner_type=payload.owner_type,
                        owner_ref=payload.owner_ref,
                        reviewer_type=payload.reviewer_type,
                        reviewer_ref=payload.reviewer_ref,
                        requires_review=payload.requires_review,
                        labels=payload.labels,
                        blocked_reason_code=blocked_reason_code_arg,
                        blocked_reason_text=blocked_reason_text_arg,
                        actor_session_id=actor.actor_session_id,
                        actor_type=actor.actor_type,
                        actor_ref=actor.actor_ref,
                        attachments=payload.attachments,
                    )
            elif effective_session_id is not None:
                item = await service.update_task(
                    profile_id=target_profile_id,
                    task_id=payload.task_id,
                    title=payload.title,
                    description=payload.description,
                    status=payload.status,
                    priority=payload.priority,
                    due_at=payload.due_at,
                    owner_type=payload.owner_type,
                    owner_ref=payload.owner_ref,
                    reviewer_type=payload.reviewer_type,
                    reviewer_ref=payload.reviewer_ref,
                    requires_review=payload.requires_review,
                    labels=payload.labels,
                    session_id=effective_session_id,
                    session_profile_id=(
                        effective_session_profile_id
                        if session_profile_id_explicit or effective_session_profile_id is not None
                        else None
                    ),
                    blocked_reason_code=blocked_reason_code_arg,
                    blocked_reason_text=blocked_reason_text_arg,
                    actor_session_id=actor.actor_session_id,
                    actor_type=actor.actor_type,
                    actor_ref=actor.actor_ref,
                    attachments=payload.attachments,
                )
            else:
                item = await service.update_task(
                    profile_id=target_profile_id,
                    task_id=payload.task_id,
                    title=payload.title,
                    description=payload.description,
                    status=payload.status,
                    priority=payload.priority,
                    due_at=payload.due_at,
                    owner_type=payload.owner_type,
                    owner_ref=payload.owner_ref,
                    reviewer_type=payload.reviewer_type,
                    reviewer_ref=payload.reviewer_ref,
                    requires_review=payload.requires_review,
                    labels=payload.labels,
                    blocked_reason_code=blocked_reason_code_arg,
                    blocked_reason_text=blocked_reason_text_arg,
                    actor_session_id=actor.actor_session_id,
                    actor_type=actor.actor_type,
                    actor_ref=actor.actor_ref,
                    attachments=payload.attachments,
                )
            return ToolResult(ok=True, payload={"task": item.model_dump(mode="json")})
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


def create_tool(settings: Settings) -> ToolBase:
    """Create task.update tool instance."""

    return TaskUpdateTool(settings=settings)
