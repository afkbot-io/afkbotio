"""Tool plugin for task.context.get."""

from __future__ import annotations

from pydantic import Field

from afkbot.services.task_flow import TaskFlowServiceError, get_task_flow_service
from afkbot.services.task_flow.contracts import TaskContextMetadata, TaskMetadata
from afkbot.services.task_flow_principals import EMPLOYEE_OWNER_TYPE
from afkbot.services.tools.base import ToolBase, ToolContext, ToolResult
from afkbot.services.tools.params import ToolParameters
from afkbot.services.tools.plugins.task_actor import (
    ensure_employee_task_read_scope,
    resolve_task_tool_actor,
)
from afkbot.services.tools.plugins.task_scope import (
    ensure_task_target_scope,
    resolve_task_target_profile,
)
from afkbot.settings import Settings


class TaskContextGetParams(ToolParameters):
    """Parameters for task.context.get tool."""

    task_id: str = Field(min_length=1, max_length=64)
    event_limit: int = Field(default=20, ge=1, le=100)
    comment_limit: int = Field(default=10, ge=1, le=50)


class TaskContextGetTool(ToolBase):
    """Return a Task Flow context bundle for one task."""

    name = "task.context.get"
    description = (
        "Return task, flow docs, task docs, dependencies, comments, events, and delegated work."
    )
    parameters_model = TaskContextGetParams

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def execute(self, ctx: ToolContext, params: ToolParameters) -> ToolResult:
        payload = (
            params
            if isinstance(params, TaskContextGetParams)
            else TaskContextGetParams.model_validate(params)
        )
        target_profile_id = resolve_task_target_profile(
            ctx=ctx,
            payload=payload,
            explicit_fields=set(getattr(payload, "model_fields_set", set())),
        )
        scope_error = ensure_task_target_scope(ctx=ctx, target_profile_id=target_profile_id)
        if scope_error is not None:
            return scope_error
        actor = resolve_task_tool_actor(ctx)
        if actor.actor_type == "human":
            return ToolResult.error(
                error_code="task_context_operator_scope_required",
                reason=(
                    "task.context.get requires a trusted Task Flow employee or automation "
                    "runtime context."
                ),
            )
        try:
            service = get_task_flow_service(self._settings)
            task = await service.get_task(profile_id=target_profile_id, task_id=payload.task_id)
            read_error = await ensure_employee_task_read_scope(
                ctx=ctx,
                settings=self._settings,
                target_profile_id=target_profile_id,
                task=task,
            )
            if read_error is not None:
                return read_error
            task_context = await service.build_task_context(
                profile_id=target_profile_id,
                task_id=payload.task_id,
                event_limit=payload.event_limit,
                comment_limit=payload.comment_limit,
            )
            context_payload = task_context.model_dump(mode="json")
            if actor.actor_type == EMPLOYEE_OWNER_TYPE:
                context_payload = await _employee_scoped_context_payload(
                    ctx=ctx,
                    settings=self._settings,
                    target_profile_id=target_profile_id,
                    task_context=task_context,
                )
            return ToolResult(
                ok=True, payload={"task_context": context_payload}
            )
        except TaskFlowServiceError as exc:
            return ToolResult.error(error_code=exc.error_code, reason=exc.reason)


async def _employee_scoped_context_payload(
    *,
    ctx: ToolContext,
    settings: Settings,
    target_profile_id: str,
    task_context: TaskContextMetadata,
) -> dict[str, object]:
    """Filter related context objects to the employee-readable task graph."""

    payload = task_context.model_dump(mode="json")
    dependency_tasks = await _readable_related_tasks(
        ctx=ctx,
        settings=settings,
        target_profile_id=target_profile_id,
        tasks=task_context.dependency_tasks,
    )
    dependent_tasks = await _readable_related_tasks(
        ctx=ctx,
        settings=settings,
        target_profile_id=target_profile_id,
        tasks=task_context.dependent_tasks,
    )
    delegated_tasks = await _readable_related_tasks(
        ctx=ctx,
        settings=settings,
        target_profile_id=target_profile_id,
        tasks=task_context.delegated_tasks,
    )
    dependency_ids = _task_payload_ids(dependency_tasks)
    dependent_ids = _task_payload_ids(dependent_tasks)
    payload["dependency_tasks"] = dependency_tasks
    payload["dependent_tasks"] = dependent_tasks
    payload["delegated_tasks"] = delegated_tasks
    payload["dependencies"] = [
        item
        for item in payload.get("dependencies", [])
        if isinstance(item, dict) and item.get("depends_on_task_id") in dependency_ids
    ]
    payload["dependents"] = [
        item
        for item in payload.get("dependents", [])
        if isinstance(item, dict) and item.get("task_id") in dependent_ids
    ]
    task_payload = payload.get("task")
    if isinstance(task_payload, dict):
        task_payload["depends_on_task_ids"] = [
            task_id
            for task_id in task_payload.get("depends_on_task_ids", [])
            if task_id in dependency_ids
        ]
        block_state = task_payload.get("block_state")
        if isinstance(block_state, dict):
            block_state["depends_on_task_ids"] = [
                task_id
                for task_id in block_state.get("depends_on_task_ids", [])
                if task_id in dependency_ids
            ]
    payload["recent_events"] = [
        _redact_event_payload(item, readable_dependency_ids=dependency_ids)
        for item in payload.get("recent_events", [])
        if isinstance(item, dict)
    ]
    return payload


def _task_payload_ids(tasks: list[dict[str, object]]) -> set[str]:
    """Return string ids from serialized task payloads."""

    return {task_id for task in tasks if isinstance((task_id := task.get("id")), str)}


async def _readable_related_tasks(
    *,
    ctx: ToolContext,
    settings: Settings,
    target_profile_id: str,
    tasks: tuple[TaskMetadata, ...],
) -> list[dict[str, object]]:
    readable: list[dict[str, object]] = []
    for task in tasks:
        read_error = await ensure_employee_task_read_scope(
            ctx=ctx,
            settings=settings,
            target_profile_id=target_profile_id,
            task=task,
        )
        if read_error is None:
            readable.append(_redact_related_task_payload(task.model_dump(mode="json")))
    return readable


def _redact_related_task_payload(payload: dict[str, object]) -> dict[str, object]:
    """Remove cross-session execution identifiers from neighboring task metadata."""

    redacted = dict(payload)
    for key in (
        "last_session_id",
        "last_session_profile_id",
        "active_session",
        "last_run_id",
        "last_error_code",
        "last_error_text",
    ):
        redacted[key] = None
    return redacted


def _redact_event_payload(
    payload: dict[str, object],
    *,
    readable_dependency_ids: set[str],
) -> dict[str, object]:
    redacted = dict(payload)
    details = redacted.get("details")
    if isinstance(details, dict):
        redacted_details = dict(details)
        depends_on_task_id = redacted_details.get("depends_on_task_id")
        if depends_on_task_id not in readable_dependency_ids:
            redacted_details.pop("depends_on_task_id", None)
        redacted["details"] = redacted_details
    return redacted


def create_tool(settings: Settings) -> ToolBase:
    """Create task.context.get tool instance."""

    return TaskContextGetTool(settings=settings)
