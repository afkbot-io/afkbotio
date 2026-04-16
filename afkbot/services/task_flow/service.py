"""Task Flow service orchestration over repository and storage helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeVar, cast, overload
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap import create_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_scope
from afkbot.models.task import Task
from afkbot.models.task_attachment import TaskAttachment
from afkbot.models.task_dependency import TaskDependency
from afkbot.models.task_event import TaskEvent
from afkbot.models.task_flow import TaskFlow
from afkbot.models.task_run import TaskRun
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.chat_session_turn_queue_repo import ChatSessionTurnQueueRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository, _UNSET as _REPO_FIELD_UNSET
from afkbot.repositories.support import profile_exists
from afkbot.services.profile_runtime import get_profile_runtime_config_service
from afkbot.services.session_orchestration.service import session_turn_queue_stale_cutoff
from afkbot.services.task_flow.contracts import (
    HumanTaskInboxEventMetadata,
    HumanTaskInboxMetadata,
    HumanTaskStartupSummary,
    StaleTaskClaimMetadata,
    TaskAttachmentContent,
    TaskAttachmentCreate,
    TaskAttachmentMetadata,
    TaskBlockStateMetadata,
    TaskCommentMetadata,
    TaskBoardColumnMetadata,
    TaskBoardMetadata,
    TaskDelegationMetadata,
    TaskDependencyMetadata,
    TaskEventMetadata,
    TaskFlowMetadata,
    TaskMetadata,
    TaskRunMetadata,
    TaskSessionActivityMetadata,
)
from afkbot.services.task_flow.event_log import encode_task_event_details, record_task_event
from afkbot.services.task_flow.errors import TaskFlowServiceError
from afkbot.services.task_flow.human_ref import resolve_local_human_ref
from afkbot.settings import Settings, get_settings

_SERVICES_BY_ROOT: dict[str, "TaskFlowService"] = {}
_VALID_OWNER_TYPES = {"ai_profile", "human"}
_VALID_TASK_STATUSES = {
    "plan",
    "todo",
    "claimed",
    "running",
    "blocked",
    "review",
    "completed",
    "failed",
    "cancelled",
}
_VALID_FLOW_STATUSES = {"active", "completed", "cancelled", "archived"}
_VISIBLE_HUMAN_STATUSES = ("todo", "blocked", "review")
_HUMAN_INBOX_NOTIFICATION_EVENT_TYPES = {
    "comment_added",
    "created",
    "updated",
    "review_changes_requested",
    "execution_review_ready",
    "execution_blocked",
    "dependencies_satisfied",
}
_TASK_COMMENT_EVENT_TYPE = "comment_added"
_TASK_BOARD_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("plan", "PLAN", ("plan",)),
    ("todo", "Todo", ("todo",)),
    ("blocked", "Blocked", ("blocked",)),
    ("running", "Running", ("claimed", "running")),
    ("review", "Review", ("review",)),
    ("completed", "Completed", ("completed",)),
    ("failed", "Failed", ("failed",)),
    ("cancelled", "Cancelled", ("cancelled",)),
)
_PLAN_AI_OWNER_ERROR_CODE = "task_plan_requires_human_owner"
_PLAN_AI_OWNER_REASON = "PLAN status is human-only; assign a human owner or move task to Todo"
_MAX_TASK_ATTACHMENT_BYTES = 10 * 1024 * 1024
_MAX_TASK_ATTACHMENT_BASE64_BYTES = ((_MAX_TASK_ATTACHMENT_BYTES + 2) // 3) * 4
TValue = TypeVar("TValue")
_TASK_FIELD_UNSET = object()
TASK_FLOW_FIELD_UNSET = _TASK_FIELD_UNSET


@dataclass(frozen=True, slots=True)
class _TaskSnapshot:
    title: str
    description: str
    priority: int
    due_at: datetime | None
    ready_at: datetime | None
    owner_type: str
    owner_ref: str
    reviewer_type: str | None
    reviewer_ref: str | None
    requires_review: bool
    labels: tuple[str, ...]
    status: str
    last_session_id: str | None
    last_session_profile_id: str | None
    blocked_reason_code: str | None
    blocked_reason_text: str | None


@dataclass(frozen=True, slots=True)
class _NormalizedTaskAttachment:
    name: str
    content_type: str | None
    kind: str
    content_bytes: bytes
    byte_size: int
    sha256: str


class TaskFlowService:
    """Service for task flow CRUD and human startup summaries."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings | None = None,
        engine: AsyncEngine | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._engine = engine

    async def create_flow(
        self,
        *,
        profile_id: str,
        title: str,
        description: str | None = None,
        created_by_type: str,
        created_by_ref: str,
        actor_session_id: str | None | object = _TASK_FIELD_UNSET,
        default_owner_type: str | None = None,
        default_owner_ref: str | None = None,
        labels: Sequence[str] = (),
    ) -> TaskFlowMetadata:
        """Create one task flow container."""

        normalized_title = _normalize_required_text(title, field_name="title")
        normalized_created_by_type = _normalize_required_text(
            created_by_type,
            field_name="created_by_type",
        )
        normalized_created_by_ref = _normalize_required_text(
            created_by_ref,
            field_name="created_by_ref",
        )
        normalized_actor_session_id = (
            _normalize_optional_text(cast(str | None, actor_session_id))
            if actor_session_id is not _TASK_FIELD_UNSET
            else None
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_created_by_type,
            actor_ref=normalized_created_by_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Task flow creation requires an explicit actor identity",
        )
        _validate_owner_pair(
            owner_type=default_owner_type,
            owner_ref=default_owner_ref,
            allow_missing=True,
        )
        normalized_labels = _normalize_labels(labels)

        async def _op(repo: TaskFlowRepository) -> TaskFlowMetadata:
            await _ensure_profile_exists(repo, profile_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Task flow creation requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
            )
            await _ensure_actor_refs_exist(
                repo,
                owner_type=_normalize_optional_text(default_owner_type),
                owner_ref=_normalize_optional_text(default_owner_ref),
                reviewer_type=None,
                reviewer_ref=None,
            )
            _ensure_ai_actor_admitted_to_backlog(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                error_code="task_owner_forbidden",
                reason="AI actor is not allowed to create flows in this backlog",
            )
            _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                owner_type=_normalize_optional_text(default_owner_type),
                owner_ref=_normalize_optional_text(default_owner_ref),
            )
            row = await repo.create_flow(
                flow_id=_new_identifier("flow"),
                profile_id=profile_id,
                title=normalized_title,
                description=_normalize_optional_text(description),
                status="active",
                created_by_type=normalized_created_by_type,
                created_by_ref=normalized_created_by_ref,
                default_owner_type=_normalize_optional_text(default_owner_type),
                default_owner_ref=_normalize_optional_text(default_owner_ref),
                labels_json=json.dumps(normalized_labels),
            )
            return _to_flow_metadata(row)

        return await self._with_repo(_op)

    async def list_flows(self, *, profile_id: str) -> list[TaskFlowMetadata]:
        """List flows for one profile."""

        async def _op(repo: TaskFlowRepository) -> list[TaskFlowMetadata]:
            await _ensure_profile_exists(repo, profile_id)
            rows = await repo.list_flows(profile_id=profile_id)
            return [_to_flow_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def get_flow(self, *, profile_id: str, flow_id: str) -> TaskFlowMetadata:
        """Get one flow by id."""

        async def _op(repo: TaskFlowRepository) -> TaskFlowMetadata:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_flow(profile_id=profile_id, flow_id=flow_id)
            if row is None:
                raise TaskFlowServiceError(error_code="task_flow_not_found", reason="Task flow not found")
            return _to_flow_metadata(row)

        return await self._with_repo(_op)

    async def delete_flow(self, *, profile_id: str, flow_id: str) -> None:
        """Hard-delete one flow and all tasks inside it when no active execution is running."""

        normalized_flow_id = _normalize_required_text(flow_id, field_name="flow_id")

        async def _op(repo: TaskFlowRepository) -> None:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_flow(profile_id=profile_id, flow_id=normalized_flow_id)
            if row is None:
                raise TaskFlowServiceError(error_code="task_flow_not_found", reason="Task flow not found")
            flow_tasks = await repo.list_tasks(profile_id=profile_id, flow_id=normalized_flow_id)
            if any(task.status in {"claimed", "running"} for task in flow_tasks):
                raise TaskFlowServiceError(
                    error_code="task_flow_delete_active_conflict",
                    reason="Claimed or running tasks must finish before deleting the flow",
                )
            flow_task_ids = {task.id for task in flow_tasks}
            for task in flow_tasks:
                await _delete_task_row(
                    repo=repo,
                    row=task,
                    skip_reconcile_task_ids=flow_task_ids,
                )
            deleted = await repo.delete_flow(profile_id=profile_id, flow_id=normalized_flow_id)
            if not deleted:
                raise TaskFlowServiceError(error_code="task_flow_not_found", reason="Task flow not found")

        await self._with_repo(_op)

    async def create_task(
        self,
        *,
        profile_id: str,
        title: str,
        description: str | None = None,
        status: str | None = None,
        created_by_type: str,
        created_by_ref: str,
        actor_session_id: str | None = None,
        session_id: str | None = None,
        session_profile_id: str | None = None,
        flow_id: str | None = None,
        priority: int = 50,
        due_at: datetime | None = None,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        reviewer_type: str | None = None,
        reviewer_ref: str | None = None,
        source_type: str = "manual",
        source_ref: str | None = None,
        labels: Sequence[str] = (),
        requires_review: bool = False,
        depends_on_task_ids: Sequence[str] = (),
        attachments: Sequence[TaskAttachmentCreate | dict[str, object]] = (),
    ) -> TaskMetadata:
        """Create one task and optional dependency edges."""

        normalized_title = _normalize_required_text(title, field_name="title")
        normalized_description = _normalize_task_description(description=description, required=True)
        normalized_requested_status = _normalize_create_task_status(status)
        normalized_flow_id = _normalize_optional_text(flow_id)
        normalized_labels = _normalize_labels(labels)
        normalized_depends_on = _normalize_identifier_list(depends_on_task_ids)
        normalized_attachments = _normalize_task_attachment_inputs(attachments)
        normalized_source_type = _normalize_required_text(source_type, field_name="source_type")
        normalized_created_by_type = _normalize_required_text(created_by_type, field_name="created_by_type")
        normalized_created_by_ref = _normalize_required_text(created_by_ref, field_name="created_by_ref")
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        normalized_session_id = _normalize_optional_text(session_id)
        normalized_session_profile_id = _normalize_optional_text(session_profile_id)
        _validate_owner_pair(
            owner_type=normalized_created_by_type,
            owner_ref=normalized_created_by_ref,
            allow_missing=False,
        )
        if normalized_session_profile_id is not None and normalized_session_id is None:
            raise TaskFlowServiceError(
                error_code="task_session_profile_requires_session_id",
                reason="session_profile_id requires session_id",
            )

        async def _op(repo: TaskFlowRepository) -> TaskMetadata:
            await _ensure_profile_exists(repo, profile_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_creator_forbidden",
                reason="Task creation requires a valid public actor identity",
            )
            flow = None
            if normalized_flow_id is not None:
                flow = await repo.get_flow(profile_id=profile_id, flow_id=normalized_flow_id)
                if flow is None:
                    raise TaskFlowServiceError(
                        error_code="task_flow_not_found",
                        reason="Task flow not found",
                    )

            resolved_owner_type = _normalize_optional_text(owner_type) or _normalize_optional_text(
                flow.default_owner_type if flow is not None else None
            )
            resolved_owner_ref = _normalize_optional_text(owner_ref) or _normalize_optional_text(
                flow.default_owner_ref if flow is not None else None
            )
            if resolved_owner_type is None:
                resolved_owner_type = "ai_profile"
            if resolved_owner_ref is None:
                resolved_owner_ref = profile_id
            _validate_owner_pair(owner_type=resolved_owner_type, owner_ref=resolved_owner_ref)
            _validate_owner_pair(
                owner_type=reviewer_type,
                owner_ref=reviewer_ref,
                allow_missing=True,
            )
            _ensure_public_principal_identity(
                settings=self._settings,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_creator_forbidden",
                reason="Task creation requires a valid public actor identity",
            )
            await _ensure_principal_exists(
                repo,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
            )
            _ensure_ai_actor_admitted_to_backlog(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                error_code="task_creator_forbidden",
                reason="AI actor is not allowed to create tasks in this backlog",
            )
            await _ensure_actor_refs_exist(
                repo,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=_normalize_optional_text(reviewer_type),
                reviewer_ref=_normalize_optional_text(reviewer_ref),
            )
            _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
            )
            resolved_session_profile_id = normalized_session_profile_id
            if normalized_session_id is not None:
                if resolved_session_profile_id is None:
                    resolved_session_profile_id = _resolve_task_session_profile_id_values(
                        profile_id=profile_id,
                        owner_type=resolved_owner_type,
                        owner_ref=resolved_owner_ref,
                    )
                if resolved_session_profile_id is not None:
                    await _ensure_profile_exists(repo, resolved_session_profile_id)
                if (
                    normalized_created_by_type == "ai_profile"
                    and normalized_actor_session_id is not None
                    and normalized_session_id != normalized_actor_session_id
                ):
                    raise TaskFlowServiceError(
                        error_code="task_session_binding_forbidden",
                        reason="AI actor can bind only its current session",
                    )

            if normalized_requested_status is None:
                resolved_status = "blocked" if normalized_depends_on else "todo"
            else:
                resolved_status = normalized_requested_status
            _ensure_plan_status_owner_is_human(
                status=resolved_status,
                owner_type=resolved_owner_type,
            )
            blocked_reason_code = (
                "dependency_wait"
                if normalized_depends_on and resolved_status == "blocked"
                else None
            )
            blocked_reason_text = (
                "Waiting for dependent tasks to complete."
                if normalized_depends_on and resolved_status == "blocked"
                else None
            )
            now_utc = datetime.now(timezone.utc)
            row = await repo.create_task(
                task_id=_new_identifier("task"),
                profile_id=profile_id,
                flow_id=normalized_flow_id,
                title=normalized_title,
                description=normalized_description,
                status=resolved_status,
                priority=priority,
                due_at=due_at,
                ready_at=(
                    None
                    if normalized_depends_on or resolved_status != "todo"
                    else now_utc
                ),
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=_normalize_optional_text(reviewer_type),
                reviewer_ref=_normalize_optional_text(reviewer_ref),
                source_type=normalized_source_type,
                source_ref=_normalize_optional_text(source_ref),
                created_by_type=normalized_created_by_type,
                created_by_ref=normalized_created_by_ref,
                labels_json=json.dumps(normalized_labels),
                requires_review=bool(requires_review),
                blocked_reason_code=blocked_reason_code,
                blocked_reason_text=blocked_reason_text,
                last_session_id=normalized_session_id,
                last_session_profile_id=resolved_session_profile_id,
            )
            for dependency_task_id in normalized_depends_on:
                await _create_dependency_edge(
                    repo=repo,
                    profile_id=profile_id,
                    task_id=row.id,
                    depends_on_task_id=dependency_task_id,
                    satisfied_on_status="completed",
                )
            if normalized_depends_on:
                row = await _reconcile_task_readiness(repo=repo, task=row)
            for attachment_payload in normalized_attachments:
                await _create_task_attachment(
                    repo=repo,
                    task=row,
                    attachment=attachment_payload,
                    actor_type=normalized_created_by_type,
                    actor_ref=normalized_created_by_ref,
                )
            await record_task_event(
                repo=repo,
                task_id=row.id,
                event_type="created",
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                to_status=row.status,
                details={
                    "description_changed": True,
                    "flow_id": normalized_flow_id,
                    "owner_type": resolved_owner_type,
                    "owner_ref": resolved_owner_ref,
                    "reviewer_type": _normalize_optional_text(reviewer_type),
                    "reviewer_ref": _normalize_optional_text(reviewer_ref),
                    "priority": priority,
                    "labels": list(normalized_labels),
                    "depends_on_task_ids": list(normalized_depends_on),
                    "requires_review": bool(requires_review),
                    "attachment_count": len(normalized_attachments),
                },
            )
            return await _build_task_metadata(repo, row, settings=self._settings)

        return await self._with_repo(_op)

    async def get_task(self, *, profile_id: str, task_id: str) -> TaskMetadata:
        """Get one task by id."""

        async def _op(repo: TaskFlowRepository) -> TaskMetadata:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_task(profile_id=profile_id, task_id=task_id)
            if row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            return await _build_task_metadata(repo, row, settings=self._settings)

        return await self._with_repo(_op)

    async def list_task_attachments(
        self,
        *,
        profile_id: str,
        task_id: str,
    ) -> list[TaskAttachmentMetadata]:
        """List persisted attachments for one task."""

        async def _op(repo: TaskFlowRepository) -> list[TaskAttachmentMetadata]:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            rows = await repo.list_task_attachments(task_id=task.id)
            return [_to_task_attachment_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def get_task_attachment_content(
        self,
        *,
        profile_id: str,
        task_id: str,
        attachment_id: str,
    ) -> TaskAttachmentContent:
        """Return one attachment binary payload for API download paths."""

        async def _op(repo: TaskFlowRepository) -> TaskAttachmentContent:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            row = await repo.get_task_attachment(task_id=task.id, attachment_id=attachment_id)
            if row is None:
                raise TaskFlowServiceError(
                    error_code="task_attachment_not_found",
                    reason="Task attachment not found",
                )
            return TaskAttachmentContent(
                attachment=_to_task_attachment_metadata(row),
                content_bytes=bytes(row.content or b""),
            )

        return await self._with_repo(_op)

    async def add_task_attachment(
        self,
        *,
        profile_id: str,
        task_id: str,
        actor_type: str,
        actor_ref: str,
        attachment: TaskAttachmentCreate | dict[str, object],
        actor_session_id: str | None = None,
    ) -> TaskAttachmentMetadata:
        """Append one binary attachment to the selected task."""

        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        normalized_attachment = _normalize_task_attachment_input(attachment)
        _validate_owner_pair(
            owner_type=normalized_actor_type,
            owner_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Adding a task attachment requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> TaskAttachmentMetadata:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Adding a task attachment requires an explicit actor identity",
            )
            _ensure_task_actor_can_manage(
                row=task,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            row = await _create_task_attachment(
                repo=repo,
                task=task,
                attachment=normalized_attachment,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            return _to_task_attachment_metadata(row)

        return await self._with_repo(_op)

    async def remove_task_attachment(
        self,
        *,
        profile_id: str,
        task_id: str,
        attachment_id: str,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None = None,
    ) -> bool:
        """Delete one task attachment."""

        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        _validate_owner_pair(
            owner_type=normalized_actor_type,
            owner_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Removing a task attachment requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> bool:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Removing a task attachment requires an explicit actor identity",
            )
            _ensure_task_actor_can_manage(
                row=task,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            attachment = await repo.get_task_attachment(task_id=task.id, attachment_id=attachment_id)
            if attachment is None:
                raise TaskFlowServiceError(
                    error_code="task_attachment_not_found",
                    reason="Task attachment not found",
                )
            deleted = await repo.delete_task_attachment(task_id=task.id, attachment_id=attachment.id)
            if not deleted:
                raise TaskFlowServiceError(
                    error_code="task_attachment_not_found",
                    reason="Task attachment not found",
                )
            await record_task_event(
                repo=repo,
                task_id=task.id,
                event_type="attachment_removed",
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                message=attachment.name,
                details={
                    "attachment_id": attachment.id,
                    "name": attachment.name,
                    "content_type": attachment.content_type,
                    "kind": attachment.kind,
                    "byte_size": attachment.byte_size,
                    "sha256": attachment.sha256,
                },
            )
            return True

        return await self._with_repo(_op)

    async def list_task_session_activity(
        self,
        *,
        profile_id: str,
        task_ids: Sequence[str],
    ) -> dict[str, TaskSessionActivityMetadata]:
        """Return live session activity keyed by task id for the selected task slice."""

        normalized_task_ids = _normalize_identifier_list(task_ids)

        async def _op(repo: TaskFlowRepository) -> dict[str, TaskSessionActivityMetadata]:
            await _ensure_profile_exists(repo, profile_id)
            rows: list[Task] = []
            for task_id in normalized_task_ids:
                row = await repo.get_task(profile_id=profile_id, task_id=task_id)
                if row is None:
                    raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
                rows.append(row)
            return await _load_task_session_activity(
                repo,
                rows,
                settings=self._settings,
            )

        return await self._with_repo(_op)

    async def delete_task(self, *, profile_id: str, task_id: str) -> None:
        """Hard-delete one task after validating it is not actively executing."""

        normalized_task_id = _normalize_required_text(task_id, field_name="task_id")

        async def _op(repo: TaskFlowRepository) -> None:
            row = await _require_task(repo, profile_id=profile_id, task_id=normalized_task_id)
            if row.status in {"claimed", "running"}:
                raise TaskFlowServiceError(
                    error_code="task_delete_active_conflict",
                    reason="Claimed or running task cannot be deleted",
                )
            await _delete_task_row(repo=repo, row=row)

        await self._with_repo(_op)

    async def build_board(
        self,
        *,
        profile_id: str,
        flow_id: str | None = None,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        labels: Sequence[str] = (),
        limit_per_column: int = 20,
    ) -> TaskBoardMetadata:
        """Build one kanban-like backlog board for a Task Flow slice."""

        normalized_owner_type = _normalize_optional_text(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        if normalized_owner_type is not None or normalized_owner_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                allow_missing=False,
            )
        normalized_flow_id = _normalize_optional_text(flow_id)
        normalized_labels = _normalize_labels(labels)

        async def _op(repo: TaskFlowRepository) -> TaskBoardMetadata:
            await _ensure_profile_exists(repo, profile_id)
            if normalized_flow_id is not None:
                flow = await repo.get_flow(profile_id=profile_id, flow_id=normalized_flow_id)
                if flow is None:
                    raise TaskFlowServiceError(
                        error_code="task_flow_not_found",
                        reason="Task flow not found",
                    )
            rows = await repo.list_tasks(
                profile_id=profile_id,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                flow_id=normalized_flow_id,
            )
            filtered_rows = [
                row for row in rows if _task_matches_required_labels(row=row, labels=normalized_labels)
            ]
            now_utc = datetime.now(timezone.utc)
            column_counts: dict[str, int] = {column_id: 0 for column_id, _title, _statuses in _TASK_BOARD_COLUMNS}
            preview_rows: dict[str, list[Task]] = {column_id: [] for column_id, _title, _statuses in _TASK_BOARD_COLUMNS}
            ready_count = 0
            blocked_count = 0
            running_count = 0
            needs_review_count = 0
            overdue_count = 0

            for row in filtered_rows:
                column_id = _task_board_column_id(status=row.status)
                column_counts[column_id] = column_counts.get(column_id, 0) + 1
                if len(preview_rows[column_id]) < max(limit_per_column, 1):
                    preview_rows[column_id].append(row)
                normalized_status = str(row.status or "").strip().lower()
                if normalized_status == "todo":
                    ready_count += 1
                if normalized_status == "blocked":
                    blocked_count += 1
                if normalized_status in {"claimed", "running"}:
                    running_count += 1
                if normalized_status == "review":
                    needs_review_count += 1
                if _is_task_overdue(row=row, now_utc=now_utc):
                    overdue_count += 1

            columns: list[TaskBoardColumnMetadata] = []
            for column_id, title, statuses in _TASK_BOARD_COLUMNS:
                tasks = tuple(
                    await _build_task_metadata_many(
                        repo,
                        preview_rows.get(column_id, []),
                        settings=self._settings,
                    )
                )
                columns.append(
                    TaskBoardColumnMetadata(
                        id=column_id,
                        title=title,
                        statuses=statuses,
                        count=column_counts.get(column_id, 0),
                        tasks=tasks,
                    )
                )

            return TaskBoardMetadata(
                generated_at=now_utc,
                profile_id=profile_id,
                flow_id=normalized_flow_id,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                labels=normalized_labels,
                limit_per_column=max(limit_per_column, 1),
                total_count=len(filtered_rows),
                ready_count=ready_count,
                blocked_count=blocked_count,
                running_count=running_count,
                needs_review_count=needs_review_count,
                overdue_count=overdue_count,
                columns=tuple(columns),
            )

        return await self._with_repo(_op)

    async def list_stale_task_claims(
        self,
        *,
        profile_id: str,
        limit: int | None = None,
    ) -> tuple[StaleTaskClaimMetadata, ...]:
        """List stale AI-owned claimed/running tasks whose lease already expired."""

        async def _op(repo: TaskFlowRepository) -> tuple[StaleTaskClaimMetadata, ...]:
            await _ensure_profile_exists(repo, profile_id)
            now_utc = datetime.now(timezone.utc)
            rows = await repo.list_expired_claimed_tasks(
                now_utc=now_utc,
                profile_id=profile_id,
                limit=limit,
            )
            items = [
                await _to_stale_task_claim_metadata(
                    repo,
                    row=row,
                    now_utc=now_utc,
                    settings=self._settings,
                )
                for row in rows
            ]
            return tuple(items)

        return await self._with_repo(_op)

    async def list_task_events(
        self,
        *,
        profile_id: str,
        task_id: str,
        limit: int | None = None,
    ) -> list[TaskEventMetadata]:
        """List append-only task events for one task."""

        async def _op(repo: TaskFlowRepository) -> list[TaskEventMetadata]:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            rows = await repo.list_task_events(task_id=task.id, limit=limit)
            return [_to_task_event_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def list_task_comments(
        self,
        *,
        profile_id: str,
        task_id: str,
        limit: int | None = None,
    ) -> list[TaskCommentMetadata]:
        """List append-only task comments for one task."""

        async def _op(repo: TaskFlowRepository) -> list[TaskCommentMetadata]:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            rows = await repo.list_task_events(task_id=task.id, limit=limit)
            return [
                _to_task_comment_metadata(row)
                for row in rows
                if str(row.event_type or "").strip() == _TASK_COMMENT_EVENT_TYPE
            ]

        return await self._with_repo(_op)

    async def add_task_comment(
        self,
        *,
        profile_id: str,
        task_id: str,
        message: str,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None = None,
        comment_type: str = "note",
        task_run_id: int | None = None,
    ) -> TaskCommentMetadata:
        """Append one task comment without changing task state."""

        normalized_message = _normalize_required_text(message, field_name="message")
        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        normalized_comment_type = _normalize_required_text(comment_type, field_name="comment_type")
        _validate_owner_pair(
            owner_type=normalized_actor_type,
            owner_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Adding a task comment requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> TaskCommentMetadata:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Adding a task comment requires an explicit actor identity",
            )
            _ensure_task_actor_can_manage(
                row=task,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_actor_refs_exist(
                repo,
                owner_type=normalized_actor_type,
                owner_ref=normalized_actor_ref,
                reviewer_type=None,
                reviewer_ref=None,
            )
            if task_run_id is not None:
                task_run = await repo.get_task_run(task_run_id=task_run_id, task_id=task.id)
                if task_run is None:
                    raise TaskFlowServiceError(
                        error_code="task_run_not_found",
                        reason="Task run not found",
                    )
            row = await _append_task_comment_event(
                repo=repo,
                task_id=task.id,
                task_run_id=task_run_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                message=normalized_message,
                comment_type=normalized_comment_type,
            )
            return _to_task_comment_metadata(row)

        return await self._with_repo(_op)

    async def list_review_tasks(
        self,
        *,
        profile_id: str,
        actor_type: str,
        actor_ref: str,
        flow_id: str | None = None,
        labels: Sequence[str] = (),
        limit: int | None = None,
    ) -> list[TaskMetadata]:
        """List review-queue tasks for one reviewer/actor inbox."""

        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        _validate_owner_pair(
            owner_type=normalized_actor_type,
            owner_ref=normalized_actor_ref,
            allow_missing=False,
        )
        normalized_flow_id = _normalize_optional_text(flow_id)
        normalized_labels = _normalize_labels(labels)

        async def _op(repo: TaskFlowRepository) -> list[TaskMetadata]:
            await _ensure_profile_exists(repo, profile_id)
            if normalized_flow_id is not None:
                flow = await repo.get_flow(profile_id=profile_id, flow_id=normalized_flow_id)
                if flow is None:
                    raise TaskFlowServiceError(
                        error_code="task_flow_not_found",
                        reason="Task flow not found",
                    )
            rows = await repo.list_tasks(
                profile_id=profile_id,
                statuses=("review",),
                flow_id=normalized_flow_id,
            )
            filtered_rows = [
                row
                for row in rows
                if _task_matches_required_labels(row=row, labels=normalized_labels)
                and _task_matches_review_inbox(
                    row=row,
                    actor_type=normalized_actor_type or "",
                    actor_ref=normalized_actor_ref or "",
                )
            ]
            if limit is not None:
                filtered_rows = filtered_rows[:limit]
            return await _build_task_metadata_many(
                repo,
                filtered_rows,
                settings=self._settings,
            )

        return await self._with_repo(_op)

    async def approve_review_task(
        self,
        *,
        profile_id: str,
        task_id: str,
        actor_type: str | None = None,
        actor_ref: str | None = None,
        actor_session_id: str | None = None,
    ) -> TaskMetadata:
        """Approve one review task and transition it into completed."""

        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_actor_type,
                owner_ref=normalized_actor_ref,
                allow_missing=False,
            )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_review_actor_required",
            reason="Review approval requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> TaskMetadata:
            row = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_review_actor_required",
                reason="Review approval requires an explicit actor identity",
            )
            if row.status != "review":
                raise TaskFlowServiceError(
                    error_code="task_review_invalid_state",
                    reason="Task is not in review",
                )
            before = _snapshot_task(row)
            if normalized_actor_type is not None and normalized_actor_ref is not None:
                _ensure_review_actor_matches_task(
                    row=row,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                )
            updated = await repo.update_task(
                profile_id=profile_id,
                task_id=row.id,
                status="completed",
                blocked_reason_code=None,
                blocked_reason_text=None,
            )
            if updated is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            await _reconcile_dependent_tasks(
                repo=repo,
                profile_id=profile_id,
                task_id=updated.id,
            )
            await record_task_event(
                repo=repo,
                task_id=updated.id,
                task_run_id=row.last_run_id,
                event_type="review_approved",
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                message="Review approved.",
                from_status=before.status,
                to_status=updated.status,
            )
            return await _build_task_metadata(repo, updated, settings=self._settings)

        return await self._with_repo(_op)

    async def request_review_changes(
        self,
        *,
        profile_id: str,
        task_id: str,
        reason_text: str,
        actor_type: str | None = None,
        actor_ref: str | None = None,
        actor_session_id: str | None = None,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        reason_code: str = "review_changes_requested",
    ) -> TaskMetadata:
        """Request changes for one review task and keep it non-terminal."""

        normalized_reason_text = _normalize_required_text(reason_text, field_name="reason_text")
        normalized_reason_code = _normalize_required_text(reason_code, field_name="reason_code")
        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_actor_type,
                owner_ref=normalized_actor_ref,
                allow_missing=False,
            )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_review_actor_required",
            reason="Requesting review changes requires an explicit actor identity",
        )
        normalized_owner_type = _normalize_optional_text(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        if normalized_owner_type is not None or normalized_owner_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                allow_missing=False,
            )

        async def _op(repo: TaskFlowRepository) -> TaskMetadata:
            row = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_review_actor_required",
                reason="Requesting review changes requires an explicit actor identity",
            )
            if row.status != "review":
                raise TaskFlowServiceError(
                    error_code="task_review_invalid_state",
                    reason="Task is not in review",
                )
            before = _snapshot_task(row)
            if normalized_actor_type is not None and normalized_actor_ref is not None:
                _ensure_review_actor_matches_task(
                    row=row,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                )
            effective_owner_type = normalized_owner_type or row.owner_type
            effective_owner_ref = normalized_owner_ref or row.owner_ref
            await _ensure_actor_refs_exist(
                repo,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
                reviewer_type=row.reviewer_type,
                reviewer_ref=row.reviewer_ref,
            )
            _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
            )
            updated = await repo.update_task(
                profile_id=profile_id,
                task_id=row.id,
                status="blocked",
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                blocked_reason_code=normalized_reason_code,
                blocked_reason_text=normalized_reason_text,
            )
            if updated is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            await record_task_event(
                repo=repo,
                task_id=updated.id,
                task_run_id=row.last_run_id,
                event_type="review_changes_requested",
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                message=normalized_reason_text,
                from_status=before.status,
                to_status=updated.status,
                details={
                    "reason_code": normalized_reason_code,
                    "owner_type": updated.owner_type,
                    "owner_ref": updated.owner_ref,
                },
            )
            return await _build_task_metadata(repo, updated, settings=self._settings)

        return await self._with_repo(_op)

    async def list_dependencies(
        self,
        *,
        profile_id: str,
        task_id: str,
    ) -> list[TaskDependencyMetadata]:
        """List dependency edges for one task."""

        async def _op(repo: TaskFlowRepository) -> list[TaskDependencyMetadata]:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            dependencies = await repo.list_dependencies(task_id=task.id)
            return [_to_dependency_metadata(edge) for edge in dependencies]

        return await self._with_repo(_op)

    async def add_dependency(
        self,
        *,
        profile_id: str,
        task_id: str,
        depends_on_task_id: str,
        satisfied_on_status: str = "completed",
        actor_type: str | None = None,
        actor_ref: str | None = None,
        actor_session_id: str | None = None,
    ) -> TaskDependencyMetadata:
        """Add one dependency edge and reconcile task readiness."""

        normalized_status = _normalize_status(satisfied_on_status)
        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_actor_type,
                owner_ref=normalized_actor_ref,
                allow_missing=False,
            )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Adding a dependency requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> TaskDependencyMetadata:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Adding a dependency requires an explicit actor identity",
            )
            _ensure_task_actor_can_manage(
                row=task,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _create_dependency_edge(
                repo=repo,
                profile_id=profile_id,
                task_id=task.id,
                depends_on_task_id=depends_on_task_id,
                satisfied_on_status=normalized_status,
            )
            await record_task_event(
                repo=repo,
                task_id=task.id,
                event_type="dependency_added",
                details={
                    "depends_on_task_id": depends_on_task_id,
                    "satisfied_on_status": normalized_status,
                },
            )
            refreshed_task = await repo.get_task(profile_id=profile_id, task_id=task.id)
            if refreshed_task is not None:
                await _reconcile_task_readiness_after_dependency_change(
                    repo=repo,
                    task=refreshed_task,
                )
            edge = await repo.get_dependency(
                task_id=task.id,
                depends_on_task_id=depends_on_task_id,
            )
            if edge is None:
                raise TaskFlowServiceError(
                    error_code="task_dependency_not_found",
                    reason="Dependency edge not found after creation",
                )
            return _to_dependency_metadata(edge)

        return await self._with_repo(_op)

    async def remove_dependency(
        self,
        *,
        profile_id: str,
        task_id: str,
        depends_on_task_id: str,
        actor_type: str | None = None,
        actor_ref: str | None = None,
        actor_session_id: str | None = None,
    ) -> bool:
        """Remove one dependency edge and reconcile task readiness."""

        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_actor_type,
                owner_ref=normalized_actor_ref,
                allow_missing=False,
            )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Removing a dependency requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> bool:
            task = await _require_task(repo, profile_id=profile_id, task_id=task_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Removing a dependency requires an explicit actor identity",
            )
            _ensure_task_actor_can_manage(
                row=task,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            deleted = await repo.delete_dependency(
                task_id=task.id,
                depends_on_task_id=depends_on_task_id,
            )
            if not deleted:
                raise TaskFlowServiceError(
                    error_code="task_dependency_not_found",
                    reason="Dependency edge not found",
                )
            await record_task_event(
                repo=repo,
                task_id=task.id,
                event_type="dependency_removed",
                details={"depends_on_task_id": depends_on_task_id},
            )
            refreshed_task = await repo.get_task(profile_id=profile_id, task_id=task.id)
            if refreshed_task is not None:
                await _reconcile_task_readiness_after_dependency_change(
                    repo=repo,
                    task=refreshed_task,
                )
            return True

        return await self._with_repo(_op)

    async def delegate_task(
        self,
        *,
        profile_id: str,
        source_task_id: str,
        delegated_owner_ref: str,
        description: str | None = None,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None = None,
        title: str | None = None,
        flow_id: str | None = None,
        priority: int | None = None,
        due_at: datetime | None = None,
        labels: Sequence[str] | None = None,
        requires_review: bool | None = None,
        wait_for_delegated_task: bool = True,
        handoff_note: str | None = None,
    ) -> TaskDelegationMetadata:
        """Create one delegated AI-owned task and optionally block the source task on it."""

        normalized_source_task_id = _normalize_required_text(source_task_id, field_name="source_task_id")
        normalized_delegate_owner_ref = _normalize_required_text(
            delegated_owner_ref,
            field_name="delegated_owner_ref",
        )
        normalized_description = _normalize_task_description(description=description, required=True)
        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        normalized_title = _normalize_optional_text(title)
        normalized_flow_id = _normalize_optional_text(flow_id)
        normalized_labels = _normalize_labels(labels) if labels is not None else None
        normalized_handoff_note = _normalize_optional_text(handoff_note)
        _validate_owner_pair(
            owner_type=normalized_actor_type,
            owner_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Delegating a task requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> TaskDelegationMetadata:
            source_task = await _require_task(repo, profile_id=profile_id, task_id=normalized_source_task_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Delegating a task requires an explicit actor identity",
            )
            if source_task.status in {"completed", "failed", "cancelled"}:
                raise TaskFlowServiceError(
                    error_code="task_delegate_invalid_state",
                    reason="Terminal task cannot delegate new work",
                )
            _ensure_task_actor_can_manage(
                row=source_task,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_actor_refs_exist(
                repo,
                owner_type=normalized_actor_type,
                owner_ref=normalized_actor_ref,
                reviewer_type=None,
                reviewer_ref=None,
            )
            await _ensure_actor_refs_exist(
                repo,
                owner_type="ai_profile",
                owner_ref=normalized_delegate_owner_ref,
                reviewer_type=None,
                reviewer_ref=None,
            )
            _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                owner_type="ai_profile",
                owner_ref=normalized_delegate_owner_ref,
            )
            delegated_flow_id = normalized_flow_id if normalized_flow_id is not None else source_task.flow_id
            if delegated_flow_id is not None:
                flow = await repo.get_flow(profile_id=profile_id, flow_id=delegated_flow_id)
                if flow is None:
                    raise TaskFlowServiceError(
                        error_code="task_flow_not_found",
                        reason="Task flow not found",
                    )
            delegated_title = normalized_title or f"{source_task.title} [{normalized_delegate_owner_ref}]"
            delegated_priority = priority if priority is not None else source_task.priority
            delegated_due_at = due_at if due_at is not None else source_task.due_at
            delegated_labels = (
                normalized_labels
                if normalized_labels is not None
                else tuple(_decode_labels(source_task.labels_json))
            )
            delegated_requires_review = (
                bool(requires_review) if requires_review is not None else False
            )
            delegated_now = datetime.now(timezone.utc)
            delegated_row = await repo.create_task(
                task_id=_new_identifier("task"),
                profile_id=profile_id,
                flow_id=delegated_flow_id,
                title=delegated_title,
                description=normalized_description,
                status="todo",
                priority=delegated_priority,
                due_at=delegated_due_at,
                ready_at=delegated_now,
                owner_type="ai_profile",
                owner_ref=normalized_delegate_owner_ref,
                reviewer_type=None,
                reviewer_ref=None,
                source_type="task_delegation",
                source_ref=source_task.id,
                created_by_type=normalized_actor_type,
                created_by_ref=normalized_actor_ref,
                labels_json=json.dumps(delegated_labels),
                requires_review=delegated_requires_review,
                blocked_reason_code=None,
                blocked_reason_text=None,
            )
            await record_task_event(
                repo=repo,
                task_id=delegated_row.id,
                event_type="created",
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                to_status=delegated_row.status,
                details={
                    "flow_id": delegated_flow_id,
                    "owner_type": delegated_row.owner_type,
                    "owner_ref": delegated_row.owner_ref,
                    "reviewer_type": delegated_row.reviewer_type,
                    "reviewer_ref": delegated_row.reviewer_ref,
                    "priority": delegated_priority,
                    "labels": list(delegated_labels),
                    "depends_on_task_ids": [],
                    "requires_review": delegated_requires_review,
                    "source_type": delegated_row.source_type,
                    "source_ref": delegated_row.source_ref,
                },
            )
            parent_comment = normalized_handoff_note or (
                f"Delegated to ai_profile:{normalized_delegate_owner_ref} as task {delegated_row.id}."
            )
            await _append_task_comment_event(
                repo=repo,
                task_id=source_task.id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                message=parent_comment,
                comment_type="delegation",
            )
            await _append_task_comment_event(
                repo=repo,
                task_id=delegated_row.id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                message=(
                    f"Delegated from task {source_task.id}. "
                    f"Upstream owner: {source_task.owner_type}:{source_task.owner_ref}."
                ),
                comment_type="delegation_brief",
            )
            dependency_metadata: TaskDependencyMetadata | None = None
            refreshed_source_task = source_task
            if wait_for_delegated_task:
                edge = await _create_dependency_edge(
                    repo=repo,
                    profile_id=profile_id,
                    task_id=source_task.id,
                    depends_on_task_id=delegated_row.id,
                    satisfied_on_status="completed",
                )
                await record_task_event(
                    repo=repo,
                    task_id=source_task.id,
                    event_type="dependency_added",
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                    details={
                        "depends_on_task_id": delegated_row.id,
                        "satisfied_on_status": "completed",
                    },
                )
                dependency_metadata = TaskDependencyMetadata(
                    task_id=source_task.id,
                    depends_on_task_id=delegated_row.id,
                    satisfied_on_status="completed",
                    created_at=edge.created_at,
                )
                refreshed_source = await repo.get_task(profile_id=profile_id, task_id=source_task.id)
                if refreshed_source is not None:
                    refreshed_source_task = await _reconcile_task_readiness_after_dependency_change(
                        repo=repo,
                        task=refreshed_source,
                    )
            return TaskDelegationMetadata(
                source_task=await _build_task_metadata(
                    repo,
                    refreshed_source_task,
                    settings=self._settings,
                ),
                delegated_task=await _build_task_metadata(
                    repo,
                    delegated_row,
                    settings=self._settings,
                ),
                dependency=dependency_metadata,
            )

        return await self._with_repo(_op)

    async def list_task_runs(
        self,
        *,
        profile_id: str,
        task_id: str | None = None,
        limit: int | None = None,
    ) -> list[TaskRunMetadata]:
        """List execution attempts for one task or profile."""

        async def _op(repo: TaskFlowRepository) -> list[TaskRunMetadata]:
            normalized_task_id = _normalize_optional_text(task_id)
            if normalized_task_id is not None:
                task = await _require_task(repo, profile_id=profile_id, task_id=normalized_task_id)
                runs = await repo.list_task_runs(
                    profile_id=profile_id,
                    task_id=task.id,
                    limit=limit,
                )
            else:
                await _ensure_profile_exists(repo, profile_id)
                runs = await repo.list_task_runs(profile_id=profile_id, limit=limit)
            return [_to_task_run_metadata(run) for run in runs]

        return await self._with_repo(_op)

    async def get_task_run(
        self,
        *,
        profile_id: str,
        task_run_id: int,
        task_id: str | None = None,
    ) -> TaskRunMetadata:
        """Get one execution attempt."""

        async def _op(repo: TaskFlowRepository) -> TaskRunMetadata:
            normalized_task_id = _normalize_optional_text(task_id)
            if normalized_task_id is not None:
                task = await _require_task(repo, profile_id=profile_id, task_id=normalized_task_id)
                run = await repo.get_task_run(task_run_id=task_run_id, task_id=task.id)
            else:
                await _ensure_profile_exists(repo, profile_id)
                run = await repo.get_task_run(task_run_id=task_run_id)
                if run is not None:
                    task_row = await repo.get_task(profile_id=profile_id, task_id=run.task_id)
                    if task_row is None:
                        run = None
            if run is None:
                raise TaskFlowServiceError(error_code="task_run_not_found", reason="Task run not found")
            return _to_task_run_metadata(run)

        return await self._with_repo(_op)

    async def list_tasks(
        self,
        *,
        profile_id: str,
        statuses: Sequence[str] = (),
        owner_type: str | None = None,
        owner_ref: str | None = None,
        flow_id: str | None = None,
        limit: int | None = None,
    ) -> list[TaskMetadata]:
        """List tasks with optional filters."""

        normalized_statuses = _normalize_statuses(statuses)
        normalized_owner_type = _normalize_optional_text(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        if normalized_owner_type is not None or normalized_owner_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                allow_missing=False,
            )

        async def _op(repo: TaskFlowRepository) -> list[TaskMetadata]:
            await _ensure_profile_exists(repo, profile_id)
            rows = await repo.list_tasks(
                profile_id=profile_id,
                statuses=normalized_statuses or None,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                flow_id=_normalize_optional_text(flow_id),
                limit=limit,
            )
            return await _build_task_metadata_many(
                repo,
                rows,
                settings=self._settings,
            )

        return await self._with_repo(_op)

    async def update_task(
        self,
        *,
        profile_id: str,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        due_at: datetime | None = None,
        ready_at: datetime | None | object = _TASK_FIELD_UNSET,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        reviewer_type: str | None = None,
        reviewer_ref: str | None = None,
        requires_review: bool | None = None,
        labels: Sequence[str] | None = None,
        session_id: str | None | object = _TASK_FIELD_UNSET,
        session_profile_id: str | None | object = _TASK_FIELD_UNSET,
        actor_session_id: str | None | object = _TASK_FIELD_UNSET,
        blocked_reason_code: str | None | object = _TASK_FIELD_UNSET,
        blocked_reason_text: str | None | object = _TASK_FIELD_UNSET,
        actor_type: str | None = None,
        actor_ref: str | None = None,
        attachments: Sequence[TaskAttachmentCreate | dict[str, object]] = (),
    ) -> TaskMetadata:
        """Update mutable task fields."""

        normalized_status = _normalize_status(status) if status is not None else None
        normalized_title = (
            _normalize_required_text(title, field_name="title") if title is not None else None
        )
        normalized_description = (
            _normalize_task_description(description=description)
            if description is not None
            else None
        )
        normalized_attachments = _normalize_task_attachment_inputs(attachments)
        normalized_blocked_reason_code: str | None | object = _TASK_FIELD_UNSET
        if blocked_reason_code is not _TASK_FIELD_UNSET:
            normalized_blocked_reason_code = _normalize_optional_text(
                cast(str | None, blocked_reason_code)
            )
        normalized_blocked_reason_text: str | None | object = _TASK_FIELD_UNSET
        if blocked_reason_text is not _TASK_FIELD_UNSET:
            normalized_blocked_reason_text = _normalize_optional_text(
                cast(str | None, blocked_reason_text)
            )
        normalized_session_id: str | None | object = _TASK_FIELD_UNSET
        if session_id is not _TASK_FIELD_UNSET:
            normalized_session_id = _normalize_optional_text(cast(str | None, session_id))
        normalized_session_profile_id: str | None | object = _TASK_FIELD_UNSET
        if session_profile_id is not _TASK_FIELD_UNSET:
            normalized_session_profile_id = _normalize_optional_text(
                cast(str | None, session_profile_id)
            )
        normalized_actor_session_id: str | None | object = _TASK_FIELD_UNSET
        if actor_session_id is not _TASK_FIELD_UNSET:
            normalized_actor_session_id = _normalize_optional_text(
                cast(str | None, actor_session_id)
            )
        if normalized_session_profile_id is not _TASK_FIELD_UNSET and normalized_session_id is _TASK_FIELD_UNSET:
            raise TaskFlowServiceError(
                error_code="task_session_profile_requires_session_id",
                reason="session_profile_id requires session_id",
            )
        if normalized_session_id is not _TASK_FIELD_UNSET and normalized_session_id is None:
            normalized_session_profile_id = None
        if (
            normalized_blocked_reason_code is not _TASK_FIELD_UNSET
            and normalized_blocked_reason_code == "dependency_wait"
            and ready_at not in {_TASK_FIELD_UNSET, None}
        ):
            raise TaskFlowServiceError(
                error_code="task_dependency_wait_ready_at_conflict",
                reason="dependency_wait blockers cannot schedule a timed revisit",
            )
        normalized_owner_type = _normalize_optional_text(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        if normalized_owner_type is not None or normalized_owner_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                allow_missing=False,
            )
        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_actor_type,
                owner_ref=normalized_actor_ref,
                allow_missing=False,
            )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Task updates require an explicit actor identity",
        )
        _validate_owner_pair(
            owner_type=reviewer_type,
            owner_ref=reviewer_ref,
            allow_missing=True,
        )

        async def _op(repo: TaskFlowRepository) -> tuple[TaskMetadata, bool]:
            await _ensure_profile_exists(repo, profile_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Task updates require an explicit actor identity",
            )
            current_row = await repo.get_task(profile_id=profile_id, task_id=task_id)
            if current_row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            _ensure_task_actor_can_manage(
                row=current_row,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            before = _snapshot_task(current_row)
            requested_session_id = normalized_session_id
            requested_session_profile_id = normalized_session_profile_id
            owner_changed = (
                (normalized_owner_type is not None and normalized_owner_type != current_row.owner_type)
                or (normalized_owner_ref is not None and normalized_owner_ref != current_row.owner_ref)
            )
            effective_owner_type = normalized_owner_type or current_row.owner_type
            effective_owner_ref = normalized_owner_ref or current_row.owner_ref
            effective_reviewer_type = _normalize_optional_text(reviewer_type)
            effective_reviewer_ref = _normalize_optional_text(reviewer_ref)
            if reviewer_type is None and reviewer_ref is None:
                effective_reviewer_type = current_row.reviewer_type
                effective_reviewer_ref = current_row.reviewer_ref
            effective_status = normalized_status
            if owner_changed:
                if effective_status in {"claimed", "running"}:
                    raise TaskFlowServiceError(
                        error_code="task_owner_reassign_conflict",
                        reason="Running or claimed task cannot be reassigned without leaving active execution",
                    )
                if effective_status is None and current_row.status in {"claimed", "running"}:
                    effective_status = "todo"
                if requested_session_id is _TASK_FIELD_UNSET:
                    requested_session_id = None
                    requested_session_profile_id = None
            await _ensure_actor_refs_exist(
                repo,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
                reviewer_type=effective_reviewer_type,
                reviewer_ref=effective_reviewer_ref,
            )
            _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
            )
            effective_status_after_update = effective_status or current_row.status
            _ensure_plan_status_owner_is_human(
                status=effective_status_after_update,
                owner_type=effective_owner_type,
            )
            effective_session_id = (
                requested_session_id
                if requested_session_id is not _TASK_FIELD_UNSET
                else current_row.last_session_id
            )
            effective_session_id_text = (
                _normalize_optional_text(cast(str | None, effective_session_id))
                if effective_session_id is not _TASK_FIELD_UNSET
                else None
            )
            if (
                effective_owner_type == "ai_profile"
                and effective_status_after_update in {"claimed", "running"}
                and not effective_session_id_text
            ):
                raise TaskFlowServiceError(
                    error_code="task_session_required",
                    reason="Active AI tasks require a bound session_id",
                )
            if (
                effective_owner_type == "ai_profile"
                and effective_status in {"claimed", "running"}
                and await repo.has_active_ai_task(
                    profile_id=profile_id,
                    owner_ref=effective_owner_ref,
                    exclude_task_id=current_row.id,
                )
            ):
                raise TaskFlowServiceError(
                    error_code="task_owner_active_conflict",
                    reason="AI owner already has another active task",
            )
            effective_session_profile_id: str | None | object = _TASK_FIELD_UNSET
            if requested_session_id is not _TASK_FIELD_UNSET:
                if requested_session_id is None:
                    effective_session_profile_id = None
                elif requested_session_profile_id is _TASK_FIELD_UNSET:
                    effective_session_profile_id = _resolve_task_session_profile_id_values(
                        profile_id=current_row.profile_id,
                        owner_type=effective_owner_type,
                        owner_ref=effective_owner_ref,
                    )
                else:
                    effective_session_profile_id = requested_session_profile_id
                if effective_session_profile_id is not None:
                    await _ensure_profile_exists(repo, cast(str, effective_session_profile_id))
                _ensure_ai_actor_session_binding_allowed(
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                    actor_session_id=normalized_actor_session_id,
                    session_id=requested_session_id,
                    session_profile_id=effective_session_profile_id,
                )
            try:
                row = await repo.update_task(
                    profile_id=profile_id,
                    task_id=task_id,
                    title=normalized_title,
                    description=normalized_description,
                    status=effective_status,
                    priority=priority,
                    due_at=due_at,
                    ready_at=ready_at if ready_at is not _TASK_FIELD_UNSET else _REPO_FIELD_UNSET,
                    owner_type=normalized_owner_type,
                    owner_ref=normalized_owner_ref,
                    reviewer_type=_normalize_optional_text(reviewer_type),
                    reviewer_ref=_normalize_optional_text(reviewer_ref),
                    requires_review=requires_review,
                    labels_json=(json.dumps(_normalize_labels(labels)) if labels is not None else None),
                    last_session_id=(
                        requested_session_id
                        if requested_session_id is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                    last_session_profile_id=(
                        effective_session_profile_id
                        if requested_session_id is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                    blocked_reason_code=(
                        normalized_blocked_reason_code
                        if normalized_blocked_reason_code is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                    blocked_reason_text=(
                        normalized_blocked_reason_text
                        if normalized_blocked_reason_text is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                )
            except IntegrityError as exc:
                if _is_active_ai_owner_integrity_error(exc):
                    raise TaskFlowServiceError(
                        error_code="task_owner_active_conflict",
                        reason="AI owner already has another active task",
                    ) from exc
                raise
            if row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            if effective_status == "completed":
                await _reconcile_dependent_tasks(
                    repo=repo,
                    profile_id=profile_id,
                    task_id=row.id,
                )
            elif effective_status == "todo":
                row = await _reconcile_task_readiness_after_dependency_change(
                    repo=repo,
                    task=row,
                )
            for attachment_payload in normalized_attachments:
                await _create_task_attachment(
                    repo=repo,
                    task=row,
                    attachment=attachment_payload,
                    actor_type=normalized_actor_type or row.created_by_type,
                    actor_ref=normalized_actor_ref or row.created_by_ref,
                )
            update_details = _build_task_update_event_details(
                before=before,
                after=row,
                labels=labels,
            )
            if normalized_attachments:
                update_details["attachments_added"] = len(normalized_attachments)
            if update_details:
                await record_task_event(
                    repo=repo,
                    task_id=row.id,
                    event_type="updated",
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                    from_status=before.status if before.status != row.status else None,
                    to_status=row.status if before.status != row.status else None,
                    details=update_details,
                )
            refresh_schema_invariants = (
                before.owner_type == "ai_profile"
                and before.status in {"claimed", "running"}
            ) or (
                row.owner_type == "ai_profile"
                and row.status in {"claimed", "running"}
            )
            return (
                await _build_task_metadata(repo, row, settings=self._settings),
                refresh_schema_invariants,
            )

        item, refresh_schema_invariants = await self._with_repo(_op)
        if refresh_schema_invariants:
            await self._refresh_schema_invariants()
        return item

    async def block_task(
        self,
        *,
        profile_id: str,
        task_id: str,
        reason_code: str,
        reason_text: str,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None = None,
        ready_at: datetime | None | object = _TASK_FIELD_UNSET,
        owner_type: str | None = None,
        owner_ref: str | None = None,
        reviewer_type: str | None = None,
        reviewer_ref: str | None = None,
    ) -> TaskMetadata:
        """Mark one task blocked with an explicit reason and optional revisit schedule."""

        normalized_reason_code = _normalize_required_text(reason_code, field_name="reason_code")
        normalized_reason_text = _normalize_required_text(reason_text, field_name="reason_text")
        if normalized_reason_code == "dependency_wait" and ready_at not in {_TASK_FIELD_UNSET, None}:
            raise TaskFlowServiceError(
                error_code="task_dependency_wait_ready_at_conflict",
                reason="dependency_wait blockers cannot schedule a timed revisit",
            )
        return await self.update_task(
            profile_id=profile_id,
            task_id=task_id,
            status="blocked",
            ready_at=ready_at,
            owner_type=owner_type,
            owner_ref=owner_ref,
            reviewer_type=reviewer_type,
            reviewer_ref=reviewer_ref,
            blocked_reason_code=normalized_reason_code,
            blocked_reason_text=normalized_reason_text,
            actor_type=actor_type,
            actor_ref=actor_ref,
            actor_session_id=actor_session_id,
        )

    async def reconcile_completed_task(self, *, profile_id: str, task_id: str) -> None:
        """Re-evaluate dependent tasks after one task reaches completed state."""

        async def _op(repo: TaskFlowRepository) -> None:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_task(profile_id=profile_id, task_id=task_id)
            if row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            await _reconcile_dependent_tasks(
                repo=repo,
                profile_id=profile_id,
                task_id=task_id,
            )

        await self._with_repo(_op)

    async def build_human_inbox(
        self,
        *,
        profile_id: str,
        owner_ref: str,
        task_limit: int = 5,
        event_limit: int = 5,
        channel: str | None = None,
        mark_seen: bool = False,
    ) -> HumanTaskInboxMetadata:
        """Build one notification-ready human inbox summary."""

        normalized_owner_ref = _normalize_required_text(owner_ref, field_name="owner_ref")
        normalized_channel = _normalize_optional_text(channel)

        async def _op(repo: TaskFlowRepository) -> HumanTaskInboxMetadata:
            filtered_rows, metadata_rows, status_counts, overdue_count = await _build_human_task_summary(
                repo=repo,
                profile_id=profile_id,
                owner_ref=normalized_owner_ref,
                limit=max(task_limit, 1),
                settings=self._settings,
            )
            recent_events: tuple[HumanTaskInboxEventMetadata, ...] = ()
            unseen_event_count = 0
            if filtered_rows:
                task_ids = tuple(row.id for row in filtered_rows)
                title_by_task_id = {row.id: row.title for row in filtered_rows}
                cursor = None
                if normalized_channel is not None:
                    cursor = await repo.get_task_notification_cursor(
                        profile_id=profile_id,
                        actor_type="human",
                        actor_ref=normalized_owner_ref,
                        channel=normalized_channel,
                    )
                last_seen_event_id = cursor.last_seen_event_id if cursor is not None else None
                acknowledge_event_id = await repo.get_latest_task_event_id_for_tasks(
                    task_ids=task_ids,
                    after_event_id=last_seen_event_id,
                )
                should_suppress_initial_preview = normalized_channel is not None and cursor is None
                relevant_rows = []
                if not should_suppress_initial_preview:
                    relevant_rows = await repo.list_filtered_task_events_for_tasks(
                        task_ids=task_ids,
                        after_event_id=last_seen_event_id,
                        event_types=tuple(_HUMAN_INBOX_NOTIFICATION_EVENT_TYPES),
                        updated_visible_statuses=_VISIBLE_HUMAN_STATUSES,
                        updated_detail_keys=("owner", "reviewer", "blocked_reason"),
                        limit=max(event_limit, 1),
                    )
                recent_events = tuple(
                    _to_human_inbox_event_metadata(
                        row,
                        task_title=title_by_task_id.get(row.task_id, row.task_id),
                    )
                    for row in relevant_rows
                )
                unseen_event_count = 0 if should_suppress_initial_preview else await repo.count_filtered_task_events_for_tasks(
                    task_ids=task_ids,
                    after_event_id=last_seen_event_id,
                    event_types=tuple(_HUMAN_INBOX_NOTIFICATION_EVENT_TYPES),
                    updated_visible_statuses=_VISIBLE_HUMAN_STATUSES,
                    updated_detail_keys=("owner", "reviewer", "blocked_reason"),
                )
                if normalized_channel is not None and mark_seen and acknowledge_event_id is not None:
                    await repo.upsert_task_notification_cursor(
                        profile_id=profile_id,
                        actor_type="human",
                        actor_ref=normalized_owner_ref,
                        channel=normalized_channel,
                        last_seen_event_id=acknowledge_event_id,
                    )
            return HumanTaskInboxMetadata(
                owner_ref=normalized_owner_ref,
                channel=normalized_channel,
                total_count=len(filtered_rows),
                todo_count=status_counts.get("todo", 0),
                blocked_count=status_counts.get("blocked", 0),
                review_count=status_counts.get("review", 0),
                overdue_count=overdue_count,
                unseen_event_count=unseen_event_count,
                tasks=metadata_rows,
                recent_events=recent_events,
            )

        return await self._with_repo(_op)

    async def summarize_human_tasks(
        self,
        *,
        profile_id: str,
        owner_ref: str,
        limit: int = 5,
    ) -> HumanTaskStartupSummary:
        """Return one startup summary for a human inbox across assignee and review work."""

        normalized_owner_ref = _normalize_required_text(owner_ref, field_name="owner_ref")

        async def _op(repo: TaskFlowRepository) -> HumanTaskStartupSummary:
            filtered_rows, metadata_rows, status_counts, overdue_count = await _build_human_task_summary(
                repo=repo,
                profile_id=profile_id,
                owner_ref=normalized_owner_ref,
                limit=max(limit, 1),
                settings=self._settings,
            )
            return HumanTaskStartupSummary(
                owner_ref=normalized_owner_ref,
                total_count=len(filtered_rows),
                todo_count=status_counts.get("todo", 0),
                blocked_count=status_counts.get("blocked", 0),
                review_count=status_counts.get("review", 0),
                overdue_count=overdue_count,
                tasks=metadata_rows,
            )

        return await self._with_repo(_op)

    async def _with_repo(
        self,
        op: Callable[[TaskFlowRepository], Awaitable[TValue]],
    ) -> TValue:
        async with session_scope(self._session_factory) as session:
            repo = TaskFlowRepository(session)
            return await op(repo)

    async def _refresh_schema_invariants(self) -> None:
        """Re-run lightweight schema upkeep so runtime guards recover after manual resolution."""

        if self._settings is None:
            return
        engine = self._engine
        owned_engine = False
        if engine is None:
            engine = create_engine(self._settings)
            owned_engine = True
        try:
            await create_schema(engine)
        finally:
            if owned_engine:
                await engine.dispose()

    async def shutdown(self) -> None:
        """Dispose owned async engine when the service created it."""

        if self._engine is None:
            return
        await self._engine.dispose()


def get_task_flow_service(settings: Settings) -> TaskFlowService:
    """Get or create one cached task flow service for the active root."""

    key = str(settings.root_dir.resolve())
    service = _SERVICES_BY_ROOT.get(key)
    if service is None:
        engine = create_engine(settings)
        session_factory = create_session_factory(engine)
        service = TaskFlowService(session_factory=session_factory, settings=settings, engine=engine)
        _SERVICES_BY_ROOT[key] = service
    return service


def reset_task_flow_services() -> None:
    """Reset cached task flow service instances."""

    _SERVICES_BY_ROOT.clear()


async def reset_task_flow_services_async() -> None:
    """Dispose and clear cached task flow service instances."""

    services = list(_SERVICES_BY_ROOT.values())
    for service in services:
        await service.shutdown()
    _SERVICES_BY_ROOT.clear()


async def _ensure_profile_exists(repo: TaskFlowRepository, profile_id: str) -> None:
    """Require that the selected profile exists before mutating task flow state."""

    if await profile_exists(repo._session, profile_id=profile_id):
        return
    raise TaskFlowServiceError(error_code="profile_not_found", reason="Profile not found")


async def _ensure_actor_refs_exist(
    repo: TaskFlowRepository,
    *,
    owner_type: str | None,
    owner_ref: str | None,
    reviewer_type: str | None,
    reviewer_ref: str | None,
) -> None:
    """Validate AI profile references for owners/reviewers before persistence."""

    if owner_type == "ai_profile" and owner_ref is not None:
        await _ensure_profile_exists(repo, owner_ref)
    if reviewer_type == "ai_profile" and reviewer_ref is not None:
        await _ensure_profile_exists(repo, reviewer_ref)


async def _ensure_principal_exists(
    repo: TaskFlowRepository,
    *,
    actor_type: str | None,
    actor_ref: str | None,
) -> None:
    """Validate one actor principal reference when it points at an AI profile."""

    if actor_type == "ai_profile" and actor_ref is not None:
        await _ensure_profile_exists(repo, actor_ref)


async def _ensure_public_ai_principal_session(
    repo: TaskFlowRepository,
    *,
    settings: Settings | None,
    actor_type: str | None,
    actor_ref: str | None,
    actor_session_id: str | None | object,
    error_code: str,
    reason: str,
) -> None:
    """Validate that one public AI actor really owns the supplied live chat session."""

    if settings is None or not bool(settings.taskflow_public_principal_required):
        return
    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    normalized_actor_session_id = (
        _normalize_optional_text(cast(str | None, actor_session_id))
        if actor_session_id is not _TASK_FIELD_UNSET
        else None
    )
    if (
        normalized_actor_type != "ai_profile"
        or normalized_actor_ref is None
        or normalized_actor_session_id is None
    ):
        return
    session_row = await ChatSessionRepository(repo._session).get(normalized_actor_session_id)
    if session_row is None or session_row.profile_id != normalized_actor_ref:
        raise TaskFlowServiceError(error_code=error_code, reason=reason)
    activity_rows = await ChatSessionTurnQueueRepository(repo._session).list_session_activity(
        session_keys=((normalized_actor_ref, normalized_actor_session_id),),
        older_than=session_turn_queue_stale_cutoff(settings=settings),
    )
    if not any((row.queued_turn_count + row.running_turn_count) > 0 for row in activity_rows):
        raise TaskFlowServiceError(error_code=error_code, reason=reason)


def _ensure_public_principal_identity(
    *,
    settings: Settings | None,
    actor_type: str | None,
    actor_ref: str | None,
    actor_session_id: str | None | object = _TASK_FIELD_UNSET,
    error_code: str,
    reason: str,
) -> None:
    """Require one validated public principal identity on CLI/tool/API-facing service instances."""

    if settings is None or not bool(settings.taskflow_public_principal_required):
        return
    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    normalized_actor_session_id = (
        _normalize_optional_text(cast(str | None, actor_session_id))
        if actor_session_id is not _TASK_FIELD_UNSET
        else None
    )
    if normalized_actor_type is None or normalized_actor_ref is None:
        raise TaskFlowServiceError(error_code=error_code, reason=reason)
    if normalized_actor_type == "human" and normalized_actor_ref != resolve_local_human_ref(settings):
        raise TaskFlowServiceError(error_code=error_code, reason=reason)
    if normalized_actor_type == "ai_profile" and normalized_actor_session_id is None:
        raise TaskFlowServiceError(error_code=error_code, reason=reason)


def _normalize_required_text(value: str | None, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if normalized:
        return normalized
    raise TaskFlowServiceError(error_code=f"invalid_{field_name}", reason=f"{field_name} is required")


@overload
def _normalize_task_description(*, description: str | None, required: Literal[True]) -> str: ...


@overload
def _normalize_task_description(
    *,
    description: str | None,
    required: Literal[False] = False,
) -> str | None: ...


def _normalize_task_description(*, description: str | None, required: bool = False) -> str | None:
    """Normalize the canonical task description."""

    normalized_description = _normalize_optional_text(description)
    if required and normalized_description is None:
        raise TaskFlowServiceError(
            error_code="invalid_description",
            reason="description is required",
        )
    return normalized_description


def _normalize_optional_text(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_labels(labels: Sequence[str] | None) -> tuple[str, ...]:
    if not labels:
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in labels:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _normalize_identifier_list(values: Sequence[str] | None) -> tuple[str, ...]:
    return _normalize_labels(values)


def _normalize_status(status: str) -> str:
    normalized = _normalize_required_text(status, field_name="status").lower()
    if normalized not in _VALID_TASK_STATUSES:
        raise TaskFlowServiceError(error_code="invalid_status", reason=f"Unsupported task status: {status}")
    return normalized


def _normalize_statuses(statuses: Sequence[str] | None) -> tuple[str, ...]:
    if not statuses:
        return ()
    return tuple(_normalize_status(status) for status in statuses)


def _normalize_create_task_status(status: str | None) -> str | None:
    """Normalize one optional create-time task status."""

    normalized = _normalize_status(status) if status is not None else None
    if normalized in {None, "plan", "todo"}:
        return normalized
    raise TaskFlowServiceError(
        error_code="invalid_status",
        reason="New tasks may start only in PLAN or Todo",
    )


def _ensure_plan_status_owner_is_human(*, status: str | None, owner_type: str) -> None:
    """Reject PLAN assignments for AI-owned tasks so work cannot silently stall."""

    if status != "plan":
        return
    if owner_type == "human":
        return
    raise TaskFlowServiceError(
        error_code=_PLAN_AI_OWNER_ERROR_CODE,
        reason=_PLAN_AI_OWNER_REASON,
    )


def _normalize_task_attachment_input(
    attachment: TaskAttachmentCreate | dict[str, object],
) -> _NormalizedTaskAttachment:
    """Validate one attachment payload and decode it into binary content."""

    payload = (
        attachment
        if isinstance(attachment, TaskAttachmentCreate)
        else TaskAttachmentCreate.model_validate(attachment)
    )
    if len(payload.content_base64) > _MAX_TASK_ATTACHMENT_BASE64_BYTES:
        raise TaskFlowServiceError(
            error_code="task_attachment_too_large",
            reason=(
                f"Attachment {payload.name!r} exceeds the maximum encoded size of "
                f"{_MAX_TASK_ATTACHMENT_BASE64_BYTES} bytes"
            ),
        )

    try:
        content_bytes = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TaskFlowServiceError(
            error_code="invalid_attachment_content",
            reason=f"Attachment {payload.name!r} has invalid base64 content",
        ) from exc
    if not content_bytes:
        raise TaskFlowServiceError(
            error_code="invalid_attachment_content",
            reason=f"Attachment {payload.name!r} is empty",
        )
    if len(content_bytes) > _MAX_TASK_ATTACHMENT_BYTES:
        raise TaskFlowServiceError(
            error_code="task_attachment_too_large",
            reason=(
                f"Attachment {payload.name!r} exceeds the maximum size of "
                f"{_MAX_TASK_ATTACHMENT_BYTES} bytes"
            ),
        )
    return _NormalizedTaskAttachment(
        name=payload.name,
        content_type=_normalize_optional_text(payload.content_type),
        kind=_normalize_required_text(payload.kind, field_name="attachment_kind"),
        content_bytes=content_bytes,
        byte_size=len(content_bytes),
        sha256=hashlib.sha256(content_bytes).hexdigest(),
    )


def _normalize_task_attachment_inputs(
    attachments: Sequence[TaskAttachmentCreate | dict[str, object]] | None,
) -> tuple[_NormalizedTaskAttachment, ...]:
    if not attachments:
        return ()
    return tuple(_normalize_task_attachment_input(item) for item in attachments)


def _validate_owner_pair(
    *,
    owner_type: str | None,
    owner_ref: str | None,
    allow_missing: bool = False,
) -> None:
    normalized_type = _normalize_optional_text(owner_type)
    normalized_ref = _normalize_optional_text(owner_ref)
    if normalized_type is None and normalized_ref is None and allow_missing:
        return
    if normalized_type is None or normalized_ref is None:
        raise TaskFlowServiceError(
            error_code="invalid_owner",
            reason="owner_type and owner_ref must be provided together",
        )
    if normalized_type not in _VALID_OWNER_TYPES:
        raise TaskFlowServiceError(
            error_code="invalid_owner_type",
            reason=f"Unsupported owner type: {normalized_type}",
        )


def _task_actor_has_manager_scope(
    *,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
) -> bool:
    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type == "human" and normalized_actor_ref is not None:
        return True
    return normalized_actor_type == "ai_profile" and normalized_actor_ref == task_profile_id


def _ensure_task_actor_can_manage(
    *,
    row: Task,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
) -> None:
    """Allow backlog-manager AI or the owning AI worker to mutate durable task state."""

    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type is None and normalized_actor_ref is None:
        return
    if _task_actor_has_manager_scope(
        task_profile_id=task_profile_id,
        actor_type=normalized_actor_type,
        actor_ref=normalized_actor_ref,
    ):
        return
    if (
        normalized_actor_type == "ai_profile"
        and normalized_actor_ref is not None
        and row.owner_type == "ai_profile"
        and row.owner_ref == normalized_actor_ref
    ):
        return
    raise TaskFlowServiceError(
        error_code="task_actor_forbidden",
        reason="Task cannot be changed by this actor",
    )


def _ensure_ai_owner_assignment_allowed(
    *,
    settings: Settings | None,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
    owner_type: str | None,
    owner_ref: str | None,
) -> None:
    """Restrict AI ownership changes to the actor itself or configured backlog teammates."""

    normalized_owner_type = _normalize_optional_text(owner_type)
    normalized_owner_ref = _normalize_optional_text(owner_ref)
    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_owner_type != "ai_profile" or normalized_owner_ref is None:
        return
    if settings is not None:
        allowed_profiles = _taskflow_allowed_ai_profile_ids(
            settings=settings,
            profile_id=task_profile_id,
        )
        if allowed_profiles and normalized_owner_ref not in allowed_profiles:
            raise TaskFlowServiceError(
                error_code="task_owner_forbidden",
                reason="Task cannot be assigned to this ai_profile",
            )
    if normalized_actor_type == "human" and normalized_actor_ref is not None:
        return
    if normalized_actor_type != "ai_profile" or normalized_actor_ref is None:
        return
    _ensure_ai_actor_admitted_to_backlog(
        settings=settings,
        task_profile_id=task_profile_id,
        actor_type=normalized_actor_type,
        actor_ref=normalized_actor_ref,
        error_code="task_owner_forbidden",
        reason="AI actor is not allowed to assign tasks in this backlog",
    )
    if normalized_owner_ref == normalized_actor_ref:
        return
    allowed_profiles = _taskflow_allowed_ai_profile_ids(settings=settings, profile_id=task_profile_id)
    if not allowed_profiles:
        return
    if normalized_owner_ref in allowed_profiles:
        return
    raise TaskFlowServiceError(
        error_code="task_owner_forbidden",
        reason="AI actor is not allowed to assign tasks to this ai_profile",
    )


def _ensure_ai_actor_admitted_to_backlog(
    *,
    settings: Settings | None,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
    error_code: str,
    reason: str,
) -> None:
    """Allow AI actors only when they belong to the backlog's configured roster."""

    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type != "ai_profile" or normalized_actor_ref is None:
        return
    allowed_profiles = _taskflow_allowed_ai_profile_ids(settings=settings, profile_id=task_profile_id)
    if not allowed_profiles:
        return
    if normalized_actor_ref in allowed_profiles:
        return
    raise TaskFlowServiceError(error_code=error_code, reason=reason)


def _ensure_ai_actor_session_binding_allowed(
    *,
    actor_type: str | None,
    actor_ref: str | None,
    actor_session_id: str | None | object,
    session_id: str | None | object,
    session_profile_id: str | None | object,
) -> None:
    """Restrict AI actors to binding only their current live session."""

    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type != "ai_profile" or normalized_actor_ref is None:
        return
    if actor_session_id is _TASK_FIELD_UNSET or session_id is _TASK_FIELD_UNSET:
        return
    normalized_actor_session_id = _normalize_optional_text(cast(str | None, actor_session_id))
    normalized_session_id = _normalize_optional_text(cast(str | None, session_id))
    if normalized_actor_session_id is None or normalized_session_id is None:
        return
    if normalized_session_id != normalized_actor_session_id:
        raise TaskFlowServiceError(
            error_code="task_session_binding_forbidden",
            reason="AI actor can bind only its current session",
        )
    if (
        session_profile_id is not _TASK_FIELD_UNSET
        and session_profile_id is not None
        and _normalize_optional_text(cast(str | None, session_profile_id)) != normalized_actor_ref
    ):
        raise TaskFlowServiceError(
            error_code="task_session_binding_forbidden",
            reason="AI actor cannot bind its current session to another profile",
        )


def _taskflow_allowed_ai_profile_ids(
    *,
    settings: Settings | None,
    profile_id: str,
) -> tuple[str, ...]:
    """Return the AI profiles allowed to participate in one backlog."""

    team_profile_ids = _taskflow_team_profile_ids(settings=settings, profile_id=profile_id)
    if team_profile_ids is None:
        if settings is not None and bool(settings.taskflow_strict_team_profile_ids):
            return (profile_id,)
        return ()
    allowed: list[str] = [profile_id]
    seen: set[str] = {profile_id}
    for item in team_profile_ids:
        if item in seen:
            continue
        seen.add(item)
        allowed.append(item)
    return tuple(allowed)


def _taskflow_team_profile_ids(*, settings: Settings | None, profile_id: str) -> tuple[str, ...] | None:
    """Return configured teammate AI profiles for one backlog profile."""

    if settings is None:
        return None
    config = get_profile_runtime_config_service(settings).load(profile_id)
    if config is None:
        return None
    team_profile_ids = config.taskflow_team_profile_ids
    if team_profile_ids is None:
        return None
    return tuple(str(item).strip() for item in team_profile_ids if str(item).strip())


def _new_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _to_flow_metadata(row: TaskFlow) -> TaskFlowMetadata:
    return TaskFlowMetadata(
        id=row.id,
        profile_id=row.profile_id,
        title=row.title,
        description=row.description,
        status=row.status,
        created_by_type=row.created_by_type,
        created_by_ref=row.created_by_ref,
        default_owner_type=row.default_owner_type,
        default_owner_ref=row.default_owner_ref,
        labels=tuple(_decode_labels(row.labels_json)),
        closed_at=row.closed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_task_metadata(
    row: Task,
    *,
    depends_on_task_ids: tuple[str, ...] = (),
    active_session: TaskSessionActivityMetadata | None = None,
    attachment_count: int = 0,
) -> TaskMetadata:
    return TaskMetadata(
        id=row.id,
        profile_id=row.profile_id,
        flow_id=row.flow_id,
        title=row.title,
        description=row.description,
        status=row.status,
        priority=row.priority,
        due_at=row.due_at,
        ready_at=row.ready_at,
        owner_type=row.owner_type,
        owner_ref=row.owner_ref,
        reviewer_type=row.reviewer_type,
        reviewer_ref=row.reviewer_ref,
        source_type=row.source_type,
        source_ref=row.source_ref,
        created_by_type=row.created_by_type,
        created_by_ref=row.created_by_ref,
        labels=tuple(_decode_labels(row.labels_json)),
        depends_on_task_ids=depends_on_task_ids,
        requires_review=bool(row.requires_review),
        blocked_reason_code=row.blocked_reason_code,
        blocked_reason_text=row.blocked_reason_text,
        block_state=_build_task_block_state(
            row=row,
            depends_on_task_ids=depends_on_task_ids,
        ),
        current_attempt=row.current_attempt,
        last_session_id=row.last_session_id,
        last_session_profile_id=row.last_session_profile_id,
        active_session=active_session,
        last_run_id=row.last_run_id,
        last_error_code=row.last_error_code,
        last_error_text=row.last_error_text,
        attachment_count=attachment_count,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _build_task_metadata(
    repo: TaskFlowRepository,
    row: Task,
    *,
    settings: Settings | None = None,
) -> TaskMetadata:
    active_sessions = await _load_task_session_activity(
        repo,
        (row,),
        settings=settings,
    )
    dependencies = await repo.list_dependencies(task_id=row.id)
    attachment_counts = await repo.count_task_attachments_for_tasks(task_ids=(row.id,))
    return _to_task_metadata(
        row,
        depends_on_task_ids=tuple(edge.depends_on_task_id for edge in dependencies),
        active_session=active_sessions.get(row.id),
        attachment_count=attachment_counts.get(row.id, 0),
    )


async def _build_task_metadata_many(
    repo: TaskFlowRepository,
    rows: Sequence[Task],
    *,
    settings: Settings | None = None,
) -> list[TaskMetadata]:
    row_list = list(rows)
    if not row_list:
        return []
    active_sessions = await _load_task_session_activity(
        repo,
        row_list,
        settings=settings,
    )
    dependencies = await repo.list_dependencies_for_tasks(task_ids=tuple(row.id for row in row_list))
    attachment_counts = await repo.count_task_attachments_for_tasks(task_ids=tuple(row.id for row in row_list))
    dependency_ids_by_task_id: dict[str, list[str]] = {}
    for edge in dependencies:
        dependency_ids_by_task_id.setdefault(edge.task_id, []).append(edge.depends_on_task_id)
    items: list[TaskMetadata] = []
    for row in row_list:
        items.append(
            _to_task_metadata(
                row,
                depends_on_task_ids=tuple(dependency_ids_by_task_id.get(row.id, ())),
                active_session=active_sessions.get(row.id),
                attachment_count=attachment_counts.get(row.id, 0),
            )
        )
    return items


async def _load_task_session_activity(
    repo: TaskFlowRepository,
    rows: Sequence[Task],
    *,
    settings: Settings | None = None,
) -> dict[str, TaskSessionActivityMetadata]:
    """Return active session activity keyed by task id for the selected task rows."""

    row_list = list(rows)
    if not row_list:
        return {}
    session_keys_by_task_id: dict[str, tuple[str, str]] = {}
    for row in row_list:
        session_id = str(row.last_session_id or "").strip()
        if not session_id:
            continue
        session_keys_by_task_id[row.id] = (
            _resolve_task_session_profile_id(row),
            session_id,
        )
    if not session_keys_by_task_id:
        return {}

    queue_repo = ChatSessionTurnQueueRepository(repo._session)
    activity_rows = await queue_repo.list_session_activity(
        session_keys=tuple(session_keys_by_task_id.values()),
        older_than=session_turn_queue_stale_cutoff(settings=settings or get_settings()),
    )
    activity_by_key = {
        (item.profile_id, item.session_id): item
        for item in activity_rows
        if (item.queued_turn_count + item.running_turn_count) > 0
    }
    return {
        task_id: TaskSessionActivityMetadata(
            session_id=session_id,
            session_profile_id=session_profile_id,
            dialog_active=True,
            queued_turn_count=item.queued_turn_count,
            running_turn_count=item.running_turn_count,
            latest_activity_at=item.latest_activity_at,
        )
        for task_id, (session_profile_id, session_id) in session_keys_by_task_id.items()
        if (item := activity_by_key.get((session_profile_id, session_id))) is not None
    }


def _to_dependency_metadata(row: TaskDependency) -> TaskDependencyMetadata:
    return TaskDependencyMetadata(
        task_id=row.task_id,
        depends_on_task_id=row.depends_on_task_id,
        satisfied_on_status=row.satisfied_on_status,
        created_at=row.created_at,
    )


def _to_task_attachment_metadata(row: TaskAttachment) -> TaskAttachmentMetadata:
    return TaskAttachmentMetadata(
        id=row.id,
        task_id=row.task_id,
        profile_id=row.profile_id,
        name=row.name,
        content_type=row.content_type,
        kind=row.kind,
        byte_size=row.byte_size,
        sha256=row.sha256,
        created_by_type=row.created_by_type,
        created_by_ref=row.created_by_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_task_run_metadata(row: TaskRun) -> TaskRunMetadata:
    return TaskRunMetadata(
        id=row.id,
        task_id=row.task_id,
        attempt=row.attempt,
        owner_type=row.owner_type,
        owner_ref=row.owner_ref,
        execution_mode=row.execution_mode,
        status=row.status,
        session_id=row.session_id,
        run_id=row.run_id,
        worker_id=row.worker_id,
        summary=row.summary,
        error_code=row.error_code,
        error_text=row.error_text,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _build_task_block_state(
    *,
    row: Task,
    depends_on_task_ids: Sequence[str],
) -> TaskBlockStateMetadata | None:
    status = str(row.status or "").strip().lower()
    if status not in {"blocked", "review"}:
        return None
    depends_on = tuple(str(item).strip() for item in depends_on_task_ids if str(item).strip())
    reason_code = str(row.blocked_reason_code or "").strip().lower()
    owner_type = str(row.owner_type or "").strip().lower()
    reviewer_type = str(row.reviewer_type or "").strip().lower()
    waiting_for_human = (
        owner_type == "human"
        or reviewer_type == "human"
        or reason_code.startswith("awaiting_human")
        or reason_code in {"awaiting_input", "approval_required", "review_changes_requested"}
    )
    if status == "review":
        return TaskBlockStateMetadata(
            kind="review",
            waiting_for_human=waiting_for_human,
            waiting_for_dependency=False,
            retry_scheduled=False,
            ready_at=row.ready_at,
            depends_on_task_ids=depends_on,
        )
    if reason_code == "dependency_wait":
        return TaskBlockStateMetadata(
            kind="dependency_wait",
            waiting_for_human=False,
            waiting_for_dependency=True,
            retry_scheduled=False,
            ready_at=None,
            depends_on_task_ids=depends_on,
        )
    if row.ready_at is not None:
        return TaskBlockStateMetadata(
            kind="scheduled_retry",
            waiting_for_human=waiting_for_human,
            waiting_for_dependency=False,
            retry_scheduled=True,
            ready_at=row.ready_at,
            depends_on_task_ids=depends_on,
        )
    if waiting_for_human:
        return TaskBlockStateMetadata(
            kind="human_wait",
            waiting_for_human=True,
            waiting_for_dependency=False,
            retry_scheduled=False,
            ready_at=None,
            depends_on_task_ids=depends_on,
        )
    return TaskBlockStateMetadata(
        kind="blocked",
        waiting_for_human=False,
        waiting_for_dependency=False,
        retry_scheduled=False,
        ready_at=None,
        depends_on_task_ids=depends_on,
    )


def _is_active_ai_owner_integrity_error(exc: IntegrityError) -> bool:
    """Return whether one database error comes from the active AI owner uniqueness guard."""

    message = " ".join(
        str(part).strip()
        for part in (
            getattr(exc, "statement", None),
            getattr(exc, "orig", None),
            exc,
        )
        if part is not None
    ).lower()
    return "ux_task_active_ai_owner" in message or (
        ("unique constraint failed" in message or "duplicate key value violates unique constraint" in message)
        and "owner_ref" in message
        and "profile_id" in message
    )


def _to_task_event_metadata(row: TaskEvent) -> TaskEventMetadata:
    return TaskEventMetadata(
        id=row.id,
        task_id=row.task_id,
        task_run_id=row.task_run_id,
        event_type=row.event_type,
        actor_type=row.actor_type,
        actor_ref=row.actor_ref,
        message=row.message,
        from_status=row.from_status,
        to_status=row.to_status,
        details=_decode_json_object(row.details_json),
        created_at=row.created_at,
    )


def _to_human_inbox_event_metadata(
    row: TaskEvent,
    *,
    task_title: str,
) -> HumanTaskInboxEventMetadata:
    return HumanTaskInboxEventMetadata(
        id=row.id,
        task_id=row.task_id,
        task_title=task_title,
        event_type=row.event_type,
        actor_type=row.actor_type,
        actor_ref=row.actor_ref,
        message=row.message,
        from_status=row.from_status,
        to_status=row.to_status,
        details=_decode_json_object(row.details_json),
        created_at=row.created_at,
    )


def _to_task_comment_metadata(row: TaskEvent) -> TaskCommentMetadata:
    details = _decode_json_object(row.details_json)
    comment_type = str(details.get("comment_type") or "note").strip() or "note"
    return TaskCommentMetadata(
        id=row.id,
        task_id=row.task_id,
        task_run_id=row.task_run_id,
        comment_type=comment_type,
        actor_type=row.actor_type,
        actor_ref=row.actor_ref,
        message=str(row.message or "").strip(),
        created_at=row.created_at,
    )


async def _to_stale_task_claim_metadata(
    repo: TaskFlowRepository,
    *,
    row: Task,
    now_utc: datetime,
    settings: Settings | None = None,
) -> StaleTaskClaimMetadata:
    lease_until = row.lease_until or now_utc
    if lease_until.tzinfo is None:
        lease_until = lease_until.replace(tzinfo=timezone.utc)
    stale_for_sec = max(0, int((now_utc - lease_until).total_seconds()))
    return StaleTaskClaimMetadata(
        task=await _build_task_metadata(repo, row, settings=settings),
        claimed_by=row.claimed_by,
        lease_until=lease_until,
        stale_for_sec=stale_for_sec,
    )


def _decode_labels(raw_json: str) -> list[str]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def _decode_json_object(raw_json: str) -> dict[str, object]:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items()}


def _build_task_update_event_details(
    *,
    before: _TaskSnapshot,
    after: Task,
    labels: Sequence[str] | None,
) -> dict[str, object]:
    details: dict[str, object] = {}
    if before.title != after.title:
        details["title"] = {"before": before.title, "after": after.title}
    if before.description != after.description:
        details["description_changed"] = True
    if before.priority != after.priority:
        details["priority"] = {"before": before.priority, "after": after.priority}
    if before.due_at != after.due_at:
        details["due_at"] = {
            "before": before.due_at.isoformat() if before.due_at is not None else None,
            "after": after.due_at.isoformat() if after.due_at is not None else None,
        }
    if before.ready_at != after.ready_at:
        details["ready_at"] = {
            "before": before.ready_at.isoformat() if before.ready_at is not None else None,
            "after": after.ready_at.isoformat() if after.ready_at is not None else None,
        }
    if before.owner_type != after.owner_type or before.owner_ref != after.owner_ref:
        details["owner"] = {
            "before": {"type": before.owner_type, "ref": before.owner_ref},
            "after": {"type": after.owner_type, "ref": after.owner_ref},
        }
    if before.reviewer_type != after.reviewer_type or before.reviewer_ref != after.reviewer_ref:
        details["reviewer"] = {
            "before": {"type": before.reviewer_type, "ref": before.reviewer_ref},
            "after": {"type": after.reviewer_type, "ref": after.reviewer_ref},
        }
    if before.requires_review != after.requires_review:
        details["requires_review"] = {
            "before": bool(before.requires_review),
            "after": bool(after.requires_review),
        }
    if labels is not None:
        before_labels = before.labels
        after_labels = tuple(_decode_labels(after.labels_json))
        if before_labels != after_labels:
            details["labels"] = {"before": list(before_labels), "after": list(after_labels)}
    if before.status != after.status:
        details["status"] = {"before": before.status, "after": after.status}
    if (
        before.last_session_id != after.last_session_id
        or before.last_session_profile_id != after.last_session_profile_id
    ):
        details["session"] = {
            "before": {
                "session_id": before.last_session_id,
                "session_profile_id": before.last_session_profile_id,
            },
            "after": {
                "session_id": after.last_session_id,
                "session_profile_id": after.last_session_profile_id,
            },
        }
    if before.blocked_reason_code != after.blocked_reason_code or before.blocked_reason_text != after.blocked_reason_text:
        details["blocked_reason"] = {
            "before": {"code": before.blocked_reason_code, "text": before.blocked_reason_text},
            "after": {"code": after.blocked_reason_code, "text": after.blocked_reason_text},
        }
    return details


def _snapshot_task(row: Task) -> _TaskSnapshot:
    return _TaskSnapshot(
        title=row.title,
        description=row.description,
        priority=row.priority,
        due_at=row.due_at,
        ready_at=row.ready_at,
        owner_type=row.owner_type,
        owner_ref=row.owner_ref,
        reviewer_type=row.reviewer_type,
        reviewer_ref=row.reviewer_ref,
        requires_review=bool(row.requires_review),
        labels=tuple(_decode_labels(row.labels_json)),
        status=row.status,
        last_session_id=row.last_session_id,
        last_session_profile_id=row.last_session_profile_id,
        blocked_reason_code=row.blocked_reason_code,
        blocked_reason_text=row.blocked_reason_text,
    )


def _task_matches_required_labels(*, row: Task, labels: Sequence[str]) -> bool:
    if not labels:
        return True
    task_labels = set(_decode_labels(row.labels_json))
    return set(labels).issubset(task_labels)


def _resolve_task_session_profile_id(row: Task) -> str:
    return _resolve_task_session_profile_id_values(
        profile_id=row.profile_id,
        owner_type=row.owner_type,
        owner_ref=row.owner_ref,
        fallback_session_profile_id=row.last_session_profile_id,
    )


def _resolve_task_session_profile_id_values(
    *,
    profile_id: str,
    owner_type: str | None,
    owner_ref: str | None,
    fallback_session_profile_id: str | None = None,
) -> str:
    fallback = str(fallback_session_profile_id or "").strip()
    if fallback:
        return fallback
    normalized_owner_type = str(owner_type or "").strip().lower()
    normalized_owner_ref = str(owner_ref or "").strip()
    if normalized_owner_type == "ai_profile" and normalized_owner_ref:
        return normalized_owner_ref
    return profile_id


def _task_board_column_id(*, status: str) -> str:
    normalized_status = str(status or "").strip().lower()
    for column_id, _title, statuses in _TASK_BOARD_COLUMNS:
        if normalized_status in statuses:
            return column_id
    return "todo"


def _is_task_overdue(*, row: Task, now_utc: datetime) -> bool:
    if row.due_at is None:
        return False
    if row.status in {"completed", "failed", "cancelled"}:
        return False
    due_at = row.due_at
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone.utc)
    return due_at < now_utc


def _task_matches_human_inbox(*, row: Task, owner_ref: str) -> bool:
    if row.status in {"todo", "blocked"}:
        return row.owner_type == "human" and row.owner_ref == owner_ref
    if row.status == "review":
        return (
            (row.owner_type == "human" and row.owner_ref == owner_ref)
            or (row.reviewer_type == "human" and row.reviewer_ref == owner_ref)
        )
    return False


def _task_matches_review_inbox(*, row: Task, actor_type: str, actor_ref: str) -> bool:
    if row.status != "review":
        return False
    if row.reviewer_type is not None and row.reviewer_ref is not None:
        return row.reviewer_type == actor_type and row.reviewer_ref == actor_ref
    return row.owner_type == actor_type and row.owner_ref == actor_ref


def _ensure_review_actor_matches_task(*, row: Task, actor_type: str, actor_ref: str) -> None:
    if _task_matches_review_inbox(row=row, actor_type=actor_type, actor_ref=actor_ref):
        return
    raise TaskFlowServiceError(
        error_code="task_review_actor_mismatch",
        reason="Task review is not assigned to the selected actor",
    )


async def _build_human_task_summary(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    owner_ref: str,
    limit: int,
    settings: Settings | None = None,
) -> tuple[list[Task], tuple[TaskMetadata, ...], dict[str, int], int]:
    await _ensure_profile_exists(repo, profile_id)
    all_rows = await repo.list_tasks(
        profile_id=profile_id,
        statuses=_VISIBLE_HUMAN_STATUSES,
    )
    filtered_rows = [
        row for row in all_rows if _task_matches_human_inbox(row=row, owner_ref=owner_ref)
    ]
    now_utc = datetime.now(timezone.utc)
    preview_rows = filtered_rows[:limit]
    metadata_rows = tuple(
        await _build_task_metadata_many(
            repo,
            preview_rows,
            settings=settings,
        )
    )
    status_counts = {status_name: 0 for status_name in _VISIBLE_HUMAN_STATUSES}
    overdue_count = 0
    for row in filtered_rows:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
        if _is_task_overdue(row=row, now_utc=now_utc):
            overdue_count += 1
    return filtered_rows, metadata_rows, status_counts, overdue_count


async def _reconcile_dependent_tasks(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    task_id: str,
) -> None:
    """Re-evaluate dependent tasks after one prerequisite reaches completion."""

    dependents = await repo.list_dependents(depends_on_task_id=task_id)
    for edge in dependents:
        dependent_row = await repo.get_task(profile_id=profile_id, task_id=edge.task_id)
        if dependent_row is None:
            continue
        await _reconcile_task_readiness(repo=repo, task=dependent_row)


async def _reconcile_task_readiness(
    *,
    repo: TaskFlowRepository,
    task: Task,
) -> Task:
    """Promote dependency-wait tasks into `todo` once all prerequisites are satisfied."""

    dependencies = await repo.list_dependencies(task_id=task.id)
    if not dependencies:
        return task
    for edge in dependencies:
        dependency_row = await repo.get_task(profile_id=task.profile_id, task_id=edge.depends_on_task_id)
        if dependency_row is None:
            return task
        if dependency_row.status != edge.satisfied_on_status:
            return task
    if task.status != "blocked" or task.blocked_reason_code != "dependency_wait":
        return task
    before_status = task.status
    promoted = await repo.update_task(
        profile_id=task.profile_id,
        task_id=task.id,
        status="todo",
        blocked_reason_code=None,
        blocked_reason_text=None,
    )
    if promoted is not None:
        await record_task_event(
            repo=repo,
            task_id=promoted.id,
            event_type="dependencies_satisfied",
            from_status=before_status,
            to_status=promoted.status,
        )
    return task if promoted is None else promoted


async def _reconcile_task_readiness_after_dependency_change(
    *,
    repo: TaskFlowRepository,
    task: Task,
) -> Task:
    """Apply dependency state after edges were added or removed."""

    dependencies = await repo.list_dependencies(task_id=task.id)
    if not dependencies:
        if task.status == "blocked" and task.blocked_reason_code == "dependency_wait":
            before_status = task.status
            promoted = await repo.update_task(
                profile_id=task.profile_id,
                task_id=task.id,
                status="todo",
                blocked_reason_code=None,
                blocked_reason_text=None,
            )
            if promoted is not None:
                await record_task_event(
                    repo=repo,
                    task_id=promoted.id,
                    event_type="dependencies_satisfied",
                    from_status=before_status,
                    to_status=promoted.status,
                )
            return task if promoted is None else promoted
        return task
    for edge in dependencies:
        dependency_row = await repo.get_task(profile_id=task.profile_id, task_id=edge.depends_on_task_id)
        if dependency_row is None or dependency_row.status != edge.satisfied_on_status:
            if task.status != "blocked" or task.blocked_reason_code != "dependency_wait":
                before_status = task.status
                blocked = await repo.update_task(
                    profile_id=task.profile_id,
                    task_id=task.id,
                    status="blocked",
                    blocked_reason_code="dependency_wait",
                    blocked_reason_text="Waiting for dependent tasks to complete.",
                )
                if blocked is not None:
                    await record_task_event(
                        repo=repo,
                        task_id=blocked.id,
                        event_type="dependencies_blocked",
                        from_status=before_status,
                        to_status=blocked.status,
                        details={"blocked_reason_code": "dependency_wait"},
                    )
                return task if blocked is None else blocked
            return task
    return await _reconcile_task_readiness(repo=repo, task=task)


async def _require_task(
    repo: TaskFlowRepository,
    *,
    profile_id: str,
    task_id: str,
) -> Task:
    """Load one task or raise a structured service error."""

    await _ensure_profile_exists(repo, profile_id)
    row = await repo.get_task(profile_id=profile_id, task_id=task_id)
    if row is None:
        raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
    return row


async def _append_task_comment_event(
    *,
    repo: TaskFlowRepository,
    task_id: str,
    actor_type: str,
    actor_ref: str,
    message: str,
    comment_type: str,
    task_run_id: int | None = None,
) -> TaskEvent:
    return await repo.create_task_event(
        task_id=task_id,
        task_run_id=task_run_id,
        event_type=_TASK_COMMENT_EVENT_TYPE,
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=message,
        details_json=encode_task_event_details({"comment_type": comment_type}),
    )


async def _create_task_attachment(
    *,
    repo: TaskFlowRepository,
    task: Task,
    attachment: _NormalizedTaskAttachment,
    actor_type: str,
    actor_ref: str,
) -> TaskAttachment:
    """Persist one task attachment and emit a matching history event."""

    row = await repo.create_task_attachment(
        attachment_id=_new_identifier("task_attachment"),
        task_id=task.id,
        profile_id=task.profile_id,
        name=attachment.name,
        content_type=attachment.content_type,
        kind=attachment.kind,
        byte_size=attachment.byte_size,
        sha256=attachment.sha256,
        created_by_type=actor_type,
        created_by_ref=actor_ref,
        content=attachment.content_bytes,
    )
    await record_task_event(
        repo=repo,
        task_id=task.id,
        event_type="attachment_added",
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=row.name,
        details={
            "attachment_id": row.id,
            "name": row.name,
            "content_type": row.content_type,
            "kind": row.kind,
            "byte_size": row.byte_size,
            "sha256": row.sha256,
        },
    )
    return row


async def _delete_task_row(
    *,
    repo: TaskFlowRepository,
    row: Task,
    skip_reconcile_task_ids: set[str] | None = None,
) -> None:
    """Delete one task and reconcile downstream dependencies that remain in storage."""

    if row.status in {"claimed", "running"}:
        raise TaskFlowServiceError(
            error_code="task_delete_active_conflict",
            reason="Running or claimed task cannot be deleted",
        )
    dependent_edges = await repo.list_dependents(depends_on_task_id=row.id)
    skip_ids = skip_reconcile_task_ids or set()
    dependent_task_ids = tuple(
        edge.task_id
        for edge in dependent_edges
        if edge.task_id not in skip_ids
    )
    await repo.delete_task_attachments(task_id=row.id)
    await repo.delete_task_events(task_id=row.id)
    await repo.delete_task_runs(task_id=row.id)
    await repo.delete_task_dependencies(task_id=row.id)
    deleted = await repo.delete_task(profile_id=row.profile_id, task_id=row.id)
    if not deleted:
        raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
    seen: set[str] = set()
    for dependent_task_id in dependent_task_ids:
        if dependent_task_id in seen:
            continue
        seen.add(dependent_task_id)
        dependent_row = await repo.get_task(profile_id=row.profile_id, task_id=dependent_task_id)
        if dependent_row is None:
            continue
        await _reconcile_task_readiness_after_dependency_change(repo=repo, task=dependent_row)


async def _create_dependency_edge(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    task_id: str,
    depends_on_task_id: str,
    satisfied_on_status: str,
) -> TaskDependency:
    """Create one dependency edge after validation."""

    normalized_depends_on_task_id = _normalize_required_text(
        depends_on_task_id,
        field_name="depends_on_task_id",
    )
    if normalized_depends_on_task_id == task_id:
        raise TaskFlowServiceError(
            error_code="task_dependency_invalid",
            reason="Task cannot depend on itself",
        )
    dependency_row = await repo.get_task(
        profile_id=profile_id,
        task_id=normalized_depends_on_task_id,
    )
    if dependency_row is None:
        raise TaskFlowServiceError(
            error_code="task_dependency_not_found",
            reason=f"Dependency task not found: {normalized_depends_on_task_id}",
        )
    existing = await repo.get_dependency(
        task_id=task_id,
        depends_on_task_id=normalized_depends_on_task_id,
    )
    if existing is not None:
        raise TaskFlowServiceError(
            error_code="task_dependency_exists",
            reason="Dependency edge already exists",
        )
    await _ensure_no_dependency_cycle(
        repo=repo,
        profile_id=profile_id,
        task_id=task_id,
        depends_on_task_id=normalized_depends_on_task_id,
    )
    return await repo.create_dependency(
        task_id=task_id,
        depends_on_task_id=normalized_depends_on_task_id,
        satisfied_on_status=satisfied_on_status,
    )


async def _ensure_no_dependency_cycle(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    task_id: str,
    depends_on_task_id: str,
) -> None:
    """Reject dependency edges that would create a cycle."""

    pending = [depends_on_task_id]
    seen: set[str] = set()
    while pending:
        current_task_id = pending.pop()
        if current_task_id == task_id:
            raise TaskFlowServiceError(
                error_code="task_dependency_cycle",
                reason="Dependency edge would create a cycle",
            )
        if current_task_id in seen:
            continue
        seen.add(current_task_id)
        for edge in await repo.list_dependencies(task_id=current_task_id):
            dependency_row = await repo.get_task(
                profile_id=profile_id,
                task_id=edge.depends_on_task_id,
            )
            if dependency_row is None:
                continue
            pending.append(edge.depends_on_task_id)
