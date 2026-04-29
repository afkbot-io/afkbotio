"""Tool plugin for task.create."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from afkbot.services.task_flow import (
    TaskAttachmentCreate,
    TaskFlowServiceError,
    get_task_flow_service,
)
from afkbot.services.task_flow.owner_inputs import TaskOwnerInputError, resolve_task_owner_inputs
from afkbot.services.error_logging import log_exception
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import resolve_task_tool_actor
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskCreateParams(ToolParameters):
    """Parameters for task.create tool."""

    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    status: str | None = Field(default=None, max_length=32)
    flow_id: str | None = Field(default=None, max_length=64)
    priority: int = Field(default=50, ge=0)
    due_at: datetime | None = None
    owner_type: str | None = Field(default=None, max_length=32)
    owner_ref: str | None = Field(default=None, max_length=255)
    owner_profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    owner_subagent_name: str | None = Field(default=None, min_length=1, max_length=255)
    reviewer_type: str | None = Field(default=None, max_length=32)
    reviewer_ref: str | None = Field(default=None, max_length=255)
    reviewer_profile_id: str | None = Field(default=None, min_length=1, max_length=120)
    reviewer_subagent_name: str | None = Field(default=None, min_length=1, max_length=255)
    source_type: str = Field(default="manual", max_length=64)
    source_ref: str | None = Field(default=None, max_length=255)
    labels: tuple[str, ...] = ()
    requires_review: bool = False
    depends_on_task_ids: tuple[str, ...] = ()
    attachments: tuple[TaskAttachmentCreate, ...] = ()
    session_id: str | None = Field(default=None, min_length=1, max_length=255)
    session_profile_id: str | None = Field(default=None, min_length=1, max_length=120)


class TaskCreateTool(ToolBase):
    """Create one task for the active profile."""

    name = "task.create"
    description = "Create one Task Flow task."
    parameters_model = TaskCreateParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = params if isinstance(params, TaskCreateParams) else TaskCreateParams.model_validate(params)
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
                owner_profile_id=payload.owner_profile_id,
                owner_subagent_name=payload.owner_subagent_name,
            )
            resolved_reviewer_type, resolved_reviewer_ref = resolve_task_owner_inputs(
                field_prefix="reviewer",
                owner_type=payload.reviewer_type,
                owner_ref=payload.reviewer_ref,
                owner_profile_id=payload.reviewer_profile_id,
                owner_subagent_name=payload.reviewer_subagent_name,
            )
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
            if (
                actor.actor_type != "automation"
                and effective_session_id is None
                and not session_id_explicit
            ):
                effective_session_id = ctx.session_id
            if (
                effective_session_profile_id is None
                and effective_session_id is not None
                and effective_session_id.startswith("taskflow:")
                and not session_profile_id_explicit
            ):
                runtime_taskflow = ctx.runtime_metadata.get("taskflow") if isinstance(ctx.runtime_metadata, dict) else None
                if isinstance(runtime_taskflow, dict):
                        runtime_profile_id = runtime_taskflow.get("task_profile_id")
                        if isinstance(runtime_profile_id, str) and runtime_profile_id.strip():
                            effective_session_profile_id = runtime_profile_id.strip()
            if (
                effective_session_profile_id is None
                and effective_session_id is not None
                and not session_profile_id_explicit
            ):
                effective_session_profile_id = target_profile_id
            if (
                session_profile_id_explicit
                and effective_session_profile_id is not None
                and effective_session_profile_id != target_profile_id
            ):
                return ToolResult.error(
                    error_code="task_session_binding_forbidden",
                    reason="AI actor cannot bind its current session to another profile",
                )
            item = await service.create_task(
                profile_id=target_profile_id,
                title=payload.title,
                description=payload.description,
                status=payload.status,
                created_by_type=actor.actor_type,
                created_by_ref=actor.actor_ref,
                session_id=effective_session_id,
                session_profile_id=effective_session_profile_id,
                actor_session_id=actor.actor_session_id,
                flow_id=payload.flow_id,
                priority=payload.priority,
                due_at=payload.due_at,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=resolved_reviewer_type,
                reviewer_ref=resolved_reviewer_ref,
                source_type=payload.source_type,
                source_ref=payload.source_ref,
                labels=payload.labels,
                requires_review=payload.requires_review,
                depends_on_task_ids=payload.depends_on_task_ids,
                attachments=payload.attachments,
            )
            return ToolResult(ok=True, payload={"task": item.model_dump(mode="json")})
        except TaskOwnerInputError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)
        except Exception as exc:
            log_exception(
                settings=self._settings,
                component="taskflow",
                message="Unhandled task.create tool exception",
                exc=exc,
                context={"profile_id": target_profile_id, "session_id": ctx.session_id},
            )
            return ToolResult.error(
                error_code="task_create_failed",
                reason="Task creation failed. Run `afk logs` to find the diagnostic log path.",
            )


def create_tool(settings: Settings) -> ToolBase:
    """Create task.create tool instance."""

    return TaskCreateTool(settings=settings)
