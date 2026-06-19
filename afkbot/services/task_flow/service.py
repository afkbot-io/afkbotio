"""Task Flow service orchestration over repository and storage helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, TypeVar, cast, overload
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from afkbot.db.bootstrap_runtime import ensure_task_runtime_schema
from afkbot.db.engine import create_engine
from afkbot.db.session import create_session_factory, session_write_scope
from afkbot.models.task import Task
from afkbot.models.task_attachment import TaskAttachment
from afkbot.models.task_dependency import TaskDependency
from afkbot.models.task_document import TaskDocument
from afkbot.models.task_document_revision import TaskDocumentRevision
from afkbot.models.task_event import TaskEvent
from afkbot.models.task_flow import TaskFlow
from afkbot.models.task_run import TaskRun
from afkbot.repositories.chat_session_repo import ChatSessionRepository
from afkbot.repositories.chat_session_turn_queue_repo import ChatSessionTurnQueueRepository
from afkbot.repositories.task_flow_repo import TaskFlowRepository, _UNSET as _REPO_FIELD_UNSET
from afkbot.repositories.support import profile_exists
from afkbot.services.automations.principals import (
    AutomationPrincipalValidationError,
    ensure_automation_principal_exists,
    parse_automation_principal_ref,
)
from afkbot.services.employees import EmployeeMetadata, EmployeeService, EmployeeServiceError
from afkbot.services.task_flow_principals import (
    EMPLOYEE_OWNER_TYPE,
    is_employee_executor_owner_type,
    normalize_task_owner_type,
    parse_employee_owner_ref,
    resolve_employee_execution_profile_id,
)
from afkbot.services.session_orchestration.service import session_turn_queue_stale_cutoff
from afkbot.services.task_flow.contracts import (
    EmployeeTaskInboxEventMetadata,
    EmployeeTaskInboxMetadata,
    StaleTaskClaimMetadata,
    TaskAttachmentContent,
    TaskAttachmentCreate,
    TaskAttachmentMetadata,
    TaskBlockStateMetadata,
    TaskCommentMetadata,
    TaskContextMetadata,
    TaskDocumentMetadata,
    TaskDocumentRevisionMetadata,
    TaskBoardColumnMetadata,
    TaskBoardMetadata,
    TaskDelegationMetadata,
    TaskDependencyMetadata,
    TaskEventMetadata,
    TaskFlowMetadata,
    TaskKnowledgeMaintenanceFlowMetadata,
    TaskKnowledgeMaintenanceSweepMetadata,
    TaskKnowledgePacketDocumentMetadata,
    TaskKnowledgePacketMetadata,
    TaskMetadata,
    TaskRunMetadata,
    TaskSessionActivityMetadata,
)
from afkbot.services.task_flow.event_log import encode_task_event_details, record_task_event
from afkbot.services.task_flow.errors import TaskFlowServiceError
from afkbot.services.task_flow.human_ref import resolve_local_human_ref
from afkbot.services.task_flow.knowledge_spine import (
    CANONICAL_FLOW_DOCUMENTS,
    CANONICAL_FLOW_DOCUMENT_KEYS,
    KnowledgePacket,
    TASK_WORKING_DOCUMENT_KEYS,
    build_knowledge_packet,
    default_flow_document_body,
    select_canonical_flow_documents,
    select_task_working_documents,
)
from afkbot.services.task_flow.manager_intake import ensure_manager_intake_transition_allowed
from afkbot.services.task_flow.work_modes import MANAGER_INTAKE_LABELS
from afkbot.settings import Settings, get_settings

_SERVICES_BY_ROOT: dict[str, "TaskFlowService"] = {}
_VALID_OWNER_TYPES = {EMPLOYEE_OWNER_TYPE}
_VALID_ACTOR_TYPES = _VALID_OWNER_TYPES | {"automation", "human"}
_HUMAN_INTAKE_SOURCE_TYPES = {"manual", "ui_task_flow"}
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
_TASK_COMMENT_EVENT_TYPE = "comment_added"
_MANAGER_ESCALATION_BLOCKER_CODES = {
    "manager_reassignment_required",
    "orchestrator_handoff_required",
}
_MANAGER_ESCALATION_TEXT_MARKERS = (
    "task_owner_forbidden",
    "manager handoff",
    "managerial handoff",
    "orchestrator handoff",
    "менеджерский",
)
_MANAGER_ESCALATION_SOURCE_TYPE = "manager_escalation"
_MANAGER_ESCALATION_TYPE = "manager_reassignment"
_KNOWLEDGE_MAINTENANCE_SOURCE_TYPE = "knowledge_maintenance"
_KNOWLEDGE_MAINTENANCE_LABELS = ("knowledge-maintenance", "autonomous-routing")
_TASK_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_TASK_DOCUMENT_SCOPE_FLOW = "flow"
_TASK_DOCUMENT_SCOPE_TASK = "task"
_DEFAULT_FLOW_DOCUMENTS = CANONICAL_FLOW_DOCUMENTS
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
_MAX_TASK_ATTACHMENT_BYTES = 10 * 1024 * 1024
_MAX_TASK_ATTACHMENT_BASE64_BYTES = ((_MAX_TASK_ATTACHMENT_BYTES + 2) // 3) * 4
_TASK_FLOW_MIN_SCHEMA_VERSION = "1.4.2"
_TASK_FLOW_SCHEMA_INCOMPATIBLE_REASON = (
    "Task Flow schema/runtime is incompatible with this request. "
    f"AFKBOT >= {_TASK_FLOW_MIN_SCHEMA_VERSION} and `afk upgrade apply` are required."
)
TValue = TypeVar("TValue")
_TASK_FIELD_UNSET = object()
TASK_FLOW_FIELD_UNSET = _TASK_FIELD_UNSET
_MENTION_PATTERN = re.compile(r"(?<![\w./:-])@([A-Za-z0-9_.-]+)(?::|/)?([A-Za-z0-9_.-]+)?")


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


@dataclass(frozen=True, slots=True)
class _KnowledgeMaintenanceHealth:
    health_status: str
    reasons: tuple[str, ...]
    missing_flow_document_keys: tuple[str, ...]
    unconfirmed_flow_document_keys: tuple[str, ...]
    open_blocked_task_count: int
    open_review_task_count: int


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
        self._settings = settings or getattr(session_factory, "_afkbot_test_settings", None)
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
        normalized_default_owner_type = normalize_task_owner_type(default_owner_type)
        normalized_default_owner_ref = _normalize_optional_text(default_owner_ref)
        _validate_actor_pair(
            actor_type=normalized_created_by_type,
            actor_ref=normalized_created_by_ref,
            allow_missing=False,
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
            owner_type=normalized_default_owner_type,
            owner_ref=normalized_default_owner_ref,
            allow_missing=True,
        )
        normalized_labels = _normalize_labels(labels)

        async def _op(repo: TaskFlowRepository) -> TaskFlowMetadata:
            await _ensure_profile_exists(repo, profile_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Task flow creation requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
            )
            await _ensure_flow_actor_can_manage(
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
            )
            await _ensure_actor_refs_exist(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                owner_type=normalized_default_owner_type,
                owner_ref=normalized_default_owner_ref,
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
            await _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                owner_type=normalized_default_owner_type,
                owner_ref=normalized_default_owner_ref,
            )
            row = await repo.create_flow(
                flow_id=_new_identifier("flow"),
                profile_id=profile_id,
                title=normalized_title,
                description=_normalize_optional_text(description),
                status="active",
                created_by_type=normalized_created_by_type,
                created_by_ref=normalized_created_by_ref,
                default_owner_type=normalized_default_owner_type,
                default_owner_ref=normalized_default_owner_ref,
                labels_json=json.dumps(normalized_labels),
            )
            await _ensure_default_flow_documents(
                repo=repo,
                flow=row,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
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
                raise TaskFlowServiceError(
                    error_code="task_flow_not_found", reason="Task flow not found"
                )
            return _to_flow_metadata(row)

        return await self._with_repo(_op)

    async def update_flow(
        self,
        *,
        profile_id: str,
        flow_id: str,
        title: str | None | object = _TASK_FIELD_UNSET,
        description: str | None | object = _TASK_FIELD_UNSET,
        actor_type: str = "human",
        actor_ref: str = "web-user",
        actor_session_id: str | None | object = _TASK_FIELD_UNSET,
        default_owner_type: str | None | object = _TASK_FIELD_UNSET,
        default_owner_ref: str | None | object = _TASK_FIELD_UNSET,
        labels: Sequence[str] | object = _TASK_FIELD_UNSET,
    ) -> TaskFlowMetadata:
        """Update editable metadata for one task flow without changing its identity."""

        normalized_flow_id = _normalize_required_text(flow_id, field_name="flow_id")
        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        normalized_actor_session_id = (
            _normalize_optional_text(cast(str | None, actor_session_id))
            if actor_session_id is not _TASK_FIELD_UNSET
            else None
        )
        _validate_actor_pair(
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Task flow updates require an explicit actor identity",
        )
        normalized_title = (
            _normalize_required_text(cast(str, title), field_name="title")
            if title is not _TASK_FIELD_UNSET
            else _TASK_FIELD_UNSET
        )
        normalized_description = (
            _normalize_optional_text(cast(str | None, description))
            if description is not _TASK_FIELD_UNSET
            else _TASK_FIELD_UNSET
        )
        normalized_default_owner_type = (
            normalize_task_owner_type(cast(str | None, default_owner_type))
            if default_owner_type is not _TASK_FIELD_UNSET
            else _TASK_FIELD_UNSET
        )
        normalized_default_owner_ref = (
            _normalize_optional_text(cast(str | None, default_owner_ref))
            if default_owner_ref is not _TASK_FIELD_UNSET
            else _TASK_FIELD_UNSET
        )
        normalized_labels = (
            _normalize_labels(cast(Sequence[str], labels))
            if labels is not _TASK_FIELD_UNSET
            else _TASK_FIELD_UNSET
        )

        async def _op(repo: TaskFlowRepository) -> TaskFlowMetadata:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_flow(profile_id=profile_id, flow_id=normalized_flow_id)
            if row is None:
                raise TaskFlowServiceError(
                    error_code="task_flow_not_found", reason="Task flow not found"
                )
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Task flow updates require an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_flow_actor_can_manage(
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            next_default_owner_type = (
                cast(str | None, normalized_default_owner_type)
                if normalized_default_owner_type is not _TASK_FIELD_UNSET
                else row.default_owner_type
            )
            next_default_owner_ref = (
                cast(str | None, normalized_default_owner_ref)
                if normalized_default_owner_ref is not _TASK_FIELD_UNSET
                else row.default_owner_ref
            )
            _validate_owner_pair(
                owner_type=next_default_owner_type,
                owner_ref=next_default_owner_ref,
                allow_missing=True,
            )
            await _ensure_actor_refs_exist(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                owner_type=next_default_owner_type,
                owner_ref=next_default_owner_ref,
                reviewer_type=None,
                reviewer_ref=None,
            )
            updated = await repo.update_flow(
                flow=row,
                title=(
                    cast(str, normalized_title)
                    if normalized_title is not _TASK_FIELD_UNSET
                    else row.title
                ),
                description=(
                    cast(str | None, normalized_description)
                    if normalized_description is not _TASK_FIELD_UNSET
                    else row.description
                ),
                default_owner_type=next_default_owner_type,
                default_owner_ref=next_default_owner_ref,
                labels_json=json.dumps(
                    cast(tuple[str, ...], normalized_labels)
                    if normalized_labels is not _TASK_FIELD_UNSET
                    else tuple(_decode_labels(row.labels_json))
                ),
            )
            return _to_flow_metadata(updated)

        return await self._with_repo(_op)

    async def list_flow_documents(
        self,
        *,
        profile_id: str,
        flow_id: str,
    ) -> list[TaskDocumentMetadata]:
        """List editable documents attached to one flow."""

        normalized_flow_id = _normalize_required_text(flow_id, field_name="flow_id")

        async def _op(repo: TaskFlowRepository) -> list[TaskDocumentMetadata]:
            flow = await _require_flow(repo, profile_id=profile_id, flow_id=normalized_flow_id)
            await _ensure_default_flow_documents(
                repo=repo,
                flow=flow,
                actor_type=flow.created_by_type,
                actor_ref=flow.created_by_ref,
            )
            rows = await repo.list_task_documents(
                profile_id=profile_id,
                scope_type=_TASK_DOCUMENT_SCOPE_FLOW,
                scope_id=flow.id,
            )
            rows = list(select_canonical_flow_documents(rows))
            return [_to_task_document_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def list_documents(
        self,
        *,
        profile_id: str,
        scope_type: str | None = None,
        scope_id: str | None = None,
        document_key: str | None = None,
        confirmation_status: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TaskDocumentMetadata]:
        """List Task Flow documents for the profile-wide document workspace."""

        normalized_scope_type = (
            _normalize_document_scope_type(scope_type) if scope_type is not None else None
        )
        normalized_scope_id = _normalize_optional_text(scope_id)
        normalized_document_key = (
            _normalize_document_key(document_key) if document_key is not None else None
        )
        normalized_status = _normalize_optional_text(confirmation_status)
        normalized_query = _normalize_optional_text(query)
        normalized_limit = min(max(int(limit), 1), 200)
        normalized_offset = max(int(offset), 0)

        async def _op(repo: TaskFlowRepository) -> list[TaskDocumentMetadata]:
            await _ensure_profile_exists(repo, profile_id)
            rows = await repo.list_task_documents_for_profile(
                profile_id=profile_id,
                scope_type=normalized_scope_type,
                scope_id=normalized_scope_id,
                document_key=normalized_document_key,
                confirmation_status=normalized_status,
                query=normalized_query,
                limit=normalized_limit,
                offset=normalized_offset,
            )
            rows = list(_filter_current_contract_documents(rows))
            return [_to_task_document_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def get_document(
        self,
        *,
        profile_id: str,
        document_id: str,
    ) -> TaskDocumentMetadata:
        """Return one Task Flow document by id for the selected profile."""

        normalized_document_id = _normalize_required_text(document_id, field_name="document_id")

        async def _op(repo: TaskFlowRepository) -> TaskDocumentMetadata:
            document = await repo.get_task_document_by_id(
                profile_id=profile_id,
                document_id=normalized_document_id,
            )
            if document is None:
                raise TaskFlowServiceError(
                    error_code="task_document_not_found",
                    reason="Task Flow document not found",
                )
            _ensure_document_uses_current_contract(document)
            return _to_task_document_metadata(document)

        return await self._with_repo(_op)

    async def put_flow_document(
        self,
        *,
        profile_id: str,
        flow_id: str,
        document_key: str,
        title: str,
        body: str,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None = None,
        base_revision: int | None = None,
    ) -> TaskDocumentMetadata:
        """Create or update one flow document with optimistic revision checks."""

        normalized_flow_id = _normalize_required_text(flow_id, field_name="flow_id")
        return await self._put_document(
            profile_id=profile_id,
            scope_type=_TASK_DOCUMENT_SCOPE_FLOW,
            scope_id=normalized_flow_id,
            document_key=document_key,
            title=title,
            body=body,
            actor_type=actor_type,
            actor_ref=actor_ref,
            actor_session_id=actor_session_id,
            base_revision=base_revision,
        )

    async def put_task_document(
        self,
        *,
        profile_id: str,
        task_id: str,
        document_key: str,
        title: str,
        body: str,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None = None,
        base_revision: int | None = None,
    ) -> TaskDocumentMetadata:
        """Create or update one task document with optimistic revision checks."""

        normalized_task_id = _normalize_required_text(task_id, field_name="task_id")
        return await self._put_document(
            profile_id=profile_id,
            scope_type=_TASK_DOCUMENT_SCOPE_TASK,
            scope_id=normalized_task_id,
            document_key=document_key,
            title=title,
            body=body,
            actor_type=actor_type,
            actor_ref=actor_ref,
            actor_session_id=actor_session_id,
            base_revision=base_revision,
        )

    async def list_document_revisions(
        self,
        *,
        profile_id: str,
        document_id: str,
        limit: int | None = None,
    ) -> list[TaskDocumentRevisionMetadata]:
        """List immutable revisions for a document."""

        normalized_document_id = _normalize_required_text(document_id, field_name="document_id")

        async def _op(repo: TaskFlowRepository) -> list[TaskDocumentRevisionMetadata]:
            document = await repo.get_task_document_by_id(
                profile_id=profile_id,
                document_id=normalized_document_id,
            )
            if document is None:
                raise TaskFlowServiceError(
                    error_code="task_document_not_found",
                    reason="Task Flow document not found",
                )
            _ensure_document_uses_current_contract(document)
            rows = await repo.list_task_document_revisions(document_id=document.id, limit=limit)
            return [_to_task_document_revision_metadata(row) for row in rows]

        return await self._with_repo(_op)

    async def confirm_document(
        self,
        *,
        profile_id: str,
        document_id: str,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None = None,
        expected_revision: int | None = None,
    ) -> TaskDocumentMetadata:
        """Confirm the current revision of one Task Flow document."""

        normalized_document_id = _normalize_required_text(document_id, field_name="document_id")
        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        _validate_actor_pair(
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_document_actor_required",
            reason="Confirming Task Flow documents requires an explicit actor identity",
        )
        if expected_revision is not None and int(expected_revision) < 1:
            raise TaskFlowServiceError(
                error_code="task_document_invalid_revision",
                reason="expected_revision must be greater than zero",
            )

        async def _op(repo: TaskFlowRepository) -> TaskDocumentMetadata:
            document = await repo.get_task_document_by_id(
                profile_id=profile_id,
                document_id=normalized_document_id,
            )
            if document is None:
                raise TaskFlowServiceError(
                    error_code="task_document_not_found",
                    reason="Task Flow document not found",
                )
            _ensure_document_uses_current_contract(document)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_document_actor_required",
                reason="Confirming Task Flow documents requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_document_actor_can_manage(
                repo=repo,
                settings=self._settings,
                profile_id=profile_id,
                scope_type=document.scope_type,
                scope_id=document.scope_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            if expected_revision is not None and int(document.revision) != int(expected_revision):
                raise TaskFlowServiceError(
                    error_code="task_document_revision_conflict",
                    reason="Document revision changed; reload the latest revision before confirming",
                )
            document = await repo.confirm_task_document(
                document=document,
                confirmed_by_type=normalized_actor_type,
                confirmed_by_ref=normalized_actor_ref,
                confirmed_at=datetime.now(timezone.utc),
            )
            await _record_document_confirmation_event(
                repo=repo,
                document=document,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            return _to_task_document_metadata(document)

        return await self._with_repo(_op)

    async def delete_document(
        self,
        *,
        profile_id: str,
        document_id: str,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None = None,
        expected_revision: int | None = None,
    ) -> TaskDocumentMetadata:
        """Delete one Task Flow document and its revision history."""

        normalized_document_id = _normalize_required_text(document_id, field_name="document_id")
        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        _validate_actor_pair(
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_document_actor_required",
            reason="Deleting Task Flow documents requires an explicit actor identity",
        )
        if expected_revision is not None and int(expected_revision) < 1:
            raise TaskFlowServiceError(
                error_code="task_document_invalid_revision",
                reason="expected_revision must be greater than zero",
            )

        async def _op(repo: TaskFlowRepository) -> TaskDocumentMetadata:
            document = await repo.get_task_document_by_id(
                profile_id=profile_id,
                document_id=normalized_document_id,
            )
            if document is None:
                raise TaskFlowServiceError(
                    error_code="task_document_not_found",
                    reason="Task Flow document not found",
                )
            _ensure_document_uses_current_contract(document)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_document_actor_required",
                reason="Deleting Task Flow documents requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_document_actor_can_manage(
                repo=repo,
                settings=self._settings,
                profile_id=profile_id,
                scope_type=document.scope_type,
                scope_id=document.scope_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            if expected_revision is not None and int(document.revision) != int(expected_revision):
                raise TaskFlowServiceError(
                    error_code="task_document_revision_conflict",
                    reason="Document revision changed; reload the latest revision before deleting",
                )
            deleted = _to_task_document_metadata(document)
            await _record_document_deleted_event(
                repo=repo,
                document=document,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await repo.delete_task_document(document=document)
            return deleted

        return await self._with_repo(_op)

    async def _put_document(
        self,
        *,
        profile_id: str,
        scope_type: str,
        scope_id: str,
        document_key: str,
        title: str,
        body: str,
        actor_type: str,
        actor_ref: str,
        actor_session_id: str | None,
        base_revision: int | None,
    ) -> TaskDocumentMetadata:
        """Shared document create/update implementation."""

        normalized_scope_type = _normalize_document_scope_type(scope_type)
        normalized_scope_id = _normalize_required_text(scope_id, field_name="scope_id")
        normalized_document_key = _normalize_document_key(document_key)
        _ensure_canonical_document_key(
            scope_type=normalized_scope_type,
            document_key=normalized_document_key,
        )
        normalized_title = _normalize_required_text(title, field_name="title")
        normalized_body = _normalize_document_body(body)
        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        _validate_actor_pair(
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_document_actor_required",
            reason="Editing Task Flow documents requires an explicit actor identity",
        )
        if base_revision is not None and int(base_revision) < 1:
            raise TaskFlowServiceError(
                error_code="task_document_invalid_revision",
                reason="base_revision must be greater than zero",
            )

        async def _op(repo: TaskFlowRepository) -> TaskDocumentMetadata:
            await _ensure_document_scope_exists(
                repo=repo,
                profile_id=profile_id,
                scope_type=normalized_scope_type,
                scope_id=normalized_scope_id,
            )
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_document_actor_required",
                reason="Editing Task Flow documents requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_document_actor_can_manage(
                repo=repo,
                settings=self._settings,
                profile_id=profile_id,
                scope_type=normalized_scope_type,
                scope_id=normalized_scope_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            document = await repo.get_task_document(
                profile_id=profile_id,
                scope_type=normalized_scope_type,
                scope_id=normalized_scope_id,
                document_key=normalized_document_key,
            )
            if document is None:
                if base_revision is not None:
                    raise TaskFlowServiceError(
                        error_code="task_document_revision_conflict",
                        reason="Document does not exist for the supplied base_revision",
                    )
                document = await repo.create_task_document(
                    document_id=_new_identifier("doc"),
                    profile_id=profile_id,
                    scope_type=normalized_scope_type,
                    scope_id=normalized_scope_id,
                    document_key=normalized_document_key,
                    title=normalized_title,
                    body=normalized_body,
                    created_by_type=normalized_actor_type,
                    created_by_ref=normalized_actor_ref,
                )
            else:
                if base_revision is not None and int(document.revision) != int(base_revision):
                    raise TaskFlowServiceError(
                        error_code="task_document_revision_conflict",
                        reason="Document revision changed; reload the latest revision before editing",
                    )
                document = await repo.update_task_document(
                    document=document,
                    title=normalized_title,
                    body=normalized_body,
                    updated_by_type=normalized_actor_type,
                    updated_by_ref=normalized_actor_ref,
                )
            await _record_document_event(
                repo=repo,
                profile_id=profile_id,
                scope_type=normalized_scope_type,
                scope_id=normalized_scope_id,
                document=document,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            return _to_task_document_metadata(document)

        return await self._with_repo(_op)

    async def delete_flow(
        self,
        *,
        profile_id: str,
        flow_id: str,
        actor_type: str | None = None,
        actor_ref: str | None = None,
        actor_session_id: str | None | object = _TASK_FIELD_UNSET,
    ) -> None:
        """Hard-delete one flow and all tasks inside it when no active execution is running."""

        normalized_flow_id = _normalize_required_text(flow_id, field_name="flow_id")
        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        normalized_actor_session_id = (
            _normalize_optional_text(cast(str | None, actor_session_id))
            if actor_session_id is not _TASK_FIELD_UNSET
            else None
        )
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_actor_pair(
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                allow_missing=False,
            )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Task flow deletion requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> None:
            await _ensure_profile_exists(repo, profile_id)
            row = await repo.get_flow(profile_id=profile_id, flow_id=normalized_flow_id)
            if row is None:
                raise TaskFlowServiceError(
                    error_code="task_flow_not_found", reason="Task flow not found"
                )
            flow_tasks = await repo.list_tasks(profile_id=profile_id, flow_id=normalized_flow_id)
            if any(task.status in {"claimed", "running"} for task in flow_tasks):
                raise TaskFlowServiceError(
                    error_code="task_flow_delete_active_conflict",
                    reason="Claimed or running tasks must finish before deleting the flow",
                )
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Task flow deletion requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            if normalized_actor_type is not None or normalized_actor_ref is not None:
                await _ensure_flow_actor_can_manage(
                    settings=self._settings,
                    profile_id=profile_id,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
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
                raise TaskFlowServiceError(
                    error_code="task_flow_not_found", reason="Task flow not found"
                )

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
        normalized_created_by_type = _normalize_required_text(
            created_by_type, field_name="created_by_type"
        )
        normalized_created_by_ref = _normalize_required_text(
            created_by_ref, field_name="created_by_ref"
        )
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        normalized_session_id = _normalize_optional_text(session_id)
        normalized_session_profile_id = _normalize_optional_text(session_profile_id)
        normalized_requested_owner_type = normalize_task_owner_type(owner_type)
        normalized_reviewer_type = normalize_task_owner_type(reviewer_type)
        normalized_reviewer_ref = _normalize_optional_text(reviewer_ref)
        _validate_actor_pair(
            actor_type=normalized_created_by_type,
            actor_ref=normalized_created_by_ref,
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
                task_profile_id=profile_id,
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

            resolved_owner_type = normalized_requested_owner_type or normalize_task_owner_type(
                flow.default_owner_type if flow is not None else None
            )
            resolved_owner_ref = _normalize_optional_text(owner_ref) or _normalize_optional_text(
                flow.default_owner_ref if flow is not None else None
            )
            if resolved_owner_type is None:
                resolved_owner_type = EMPLOYEE_OWNER_TYPE
            if resolved_owner_ref is None:
                resolved_owner_ref = await _resolve_default_employee_owner_ref(
                    settings=self._settings,
                    profile_id=profile_id,
                )
            _validate_owner_pair(owner_type=resolved_owner_type, owner_ref=resolved_owner_ref)
            _validate_owner_pair(
                owner_type=normalized_reviewer_type,
                owner_ref=normalized_reviewer_ref,
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
                settings=self._settings,
                profile_id=profile_id,
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
                settings=self._settings,
                profile_id=profile_id,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=normalized_reviewer_type,
                reviewer_ref=normalized_reviewer_ref,
            )
            await _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_created_by_type,
                actor_ref=normalized_created_by_ref,
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
            )
            await _ensure_human_intake_targets_root_employee(
                settings=self._settings,
                profile_id=profile_id,
                source_type=normalized_source_type,
                actor_type=normalized_created_by_type,
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
                    is_employee_executor_owner_type(normalized_created_by_type)
                    and normalized_actor_session_id is not None
                    and normalized_session_id != normalized_actor_session_id
                ):
                    raise TaskFlowServiceError(
                        error_code="task_session_binding_forbidden",
                        reason="AI actor can bind only its current session",
                    )
                _ensure_public_session_binding_allowed(
                    settings=self._settings,
                    task_profile_id=profile_id,
                    actor_type=normalized_created_by_type,
                    actor_ref=normalized_created_by_ref,
                    actor_session_id=normalized_actor_session_id,
                    session_id=normalized_session_id,
                    session_profile_id=resolved_session_profile_id,
                )

            if normalized_requested_status is None:
                resolved_status = "blocked" if normalized_depends_on else "todo"
            else:
                resolved_status = normalized_requested_status
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
                ready_at=(None if normalized_depends_on or resolved_status != "todo" else now_utc),
                owner_type=resolved_owner_type,
                owner_ref=resolved_owner_ref,
                reviewer_type=normalized_reviewer_type,
                reviewer_ref=normalized_reviewer_ref,
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
                    "reviewer_type": normalized_reviewer_type,
                    "reviewer_ref": normalized_reviewer_ref,
                    "priority": priority,
                    "labels": list(normalized_labels),
                    "depends_on_task_ids": list(normalized_depends_on),
                    "requires_review": bool(requires_review),
                    "attachment_count": len(normalized_attachments),
                },
            )
            if is_employee_executor_owner_type(row.owner_type) and row.status == "todo":
                await _record_task_wake_requested(
                    repo=repo,
                    task=row,
                    reason_code="task_created",
                    actor_type=normalized_created_by_type,
                    actor_ref=normalized_created_by_ref,
                    message="Employee-owned task created and ready for execution.",
                )
            return await _build_task_metadata(repo, row, settings=self._settings)

        try:
            return await self._with_repo(_op)
        except IntegrityError as exc:
            if _is_active_ai_owner_integrity_error(exc):
                raise TaskFlowServiceError(
                    error_code="task_owner_conflict",
                    reason="Only one active employee-owned task may exist for the same owner in a profile",
                ) from exc
            schema_error = _build_task_flow_schema_error(exc)
            if schema_error is not None:
                raise schema_error from exc
            raise
        except (OperationalError, ProgrammingError) as exc:
            schema_error = _build_task_flow_schema_error(exc)
            if schema_error is not None:
                raise schema_error from exc
            raise

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
        _validate_actor_pair(
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
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
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Adding a task attachment requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_task_actor_can_manage(
                row=task,
                settings=self._settings,
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
        _validate_actor_pair(
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
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
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Removing a task attachment requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_task_actor_can_manage(
                row=task,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            attachment = await repo.get_task_attachment(
                task_id=task.id, attachment_id=attachment_id
            )
            if attachment is None:
                raise TaskFlowServiceError(
                    error_code="task_attachment_not_found",
                    reason="Task attachment not found",
                )
            deleted = await repo.delete_task_attachment(
                task_id=task.id, attachment_id=attachment.id
            )
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

    async def delete_task(
        self,
        *,
        profile_id: str,
        task_id: str,
        actor_type: str | None = None,
        actor_ref: str | None = None,
        actor_session_id: str | None | object = _TASK_FIELD_UNSET,
    ) -> None:
        """Hard-delete one task after validating it is not actively executing."""

        normalized_task_id = _normalize_required_text(task_id, field_name="task_id")
        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        normalized_actor_session_id = (
            _normalize_optional_text(cast(str | None, actor_session_id))
            if actor_session_id is not _TASK_FIELD_UNSET
            else None
        )
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_actor_pair(
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                allow_missing=False,
            )
        _ensure_public_principal_identity(
            settings=self._settings,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            actor_session_id=normalized_actor_session_id,
            error_code="task_actor_required",
            reason="Task deletion requires an explicit actor identity",
        )

        async def _op(repo: TaskFlowRepository) -> None:
            row = await _require_task(repo, profile_id=profile_id, task_id=normalized_task_id)
            if row.status in {"claimed", "running"}:
                raise TaskFlowServiceError(
                    error_code="task_delete_active_conflict",
                    reason="Claimed or running task cannot be deleted",
                )
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Task deletion requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            if normalized_actor_type is not None or normalized_actor_ref is not None:
                await _ensure_task_actor_can_manage(
                    row=row,
                    settings=self._settings,
                    task_profile_id=profile_id,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
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

        normalized_owner_type = normalize_task_owner_type(owner_type)
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
                row
                for row in rows
                if _task_matches_required_labels(row=row, labels=normalized_labels)
            ]
            now_utc = datetime.now(timezone.utc)
            column_counts: dict[str, int] = {
                column_id: 0 for column_id, _title, _statuses in _TASK_BOARD_COLUMNS
            }
            preview_rows: dict[str, list[Task]] = {
                column_id: [] for column_id, _title, _statuses in _TASK_BOARD_COLUMNS
            }
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
        owner_ref: str | None = None,
        limit: int | None = None,
    ) -> tuple[StaleTaskClaimMetadata, ...]:
        """List stale employee-owned claimed/running tasks whose lease already expired."""

        async def _op(repo: TaskFlowRepository) -> tuple[StaleTaskClaimMetadata, ...]:
            await _ensure_profile_exists(repo, profile_id)
            now_utc = datetime.now(timezone.utc)
            rows = await repo.list_expired_claimed_tasks(
                now_utc=now_utc,
                profile_id=profile_id,
                owner_ref=_normalize_optional_text(owner_ref),
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

    async def ensure_knowledge_maintenance_tasks(
        self,
        *,
        profile_id: str | None = None,
        flow_id: str | None = None,
        actor_type: str = "runtime",
        actor_ref: str = "task-flow",
        limit: int | None = None,
    ) -> TaskKnowledgeMaintenanceSweepMetadata:
        """Create or wake CTO tasks that maintain project knowledge for unhealthy flows."""

        normalized_profile_id = _normalize_optional_text(profile_id)
        normalized_flow_id = _normalize_optional_text(flow_id)
        normalized_actor_type = _normalize_required_text(actor_type, field_name="actor_type")
        normalized_actor_ref = _normalize_required_text(actor_ref, field_name="actor_ref")
        settings = self._settings or get_settings()
        normalized_limit = (
            max(1, int(limit))
            if limit is not None
            else max(1, int(settings.taskflow_knowledge_maintenance_max_flows_per_sweep))
        )

        async def _op(repo: TaskFlowRepository) -> TaskKnowledgeMaintenanceSweepMetadata:
            if normalized_profile_id is not None:
                await _ensure_profile_exists(repo, normalized_profile_id)
            if normalized_flow_id is not None and normalized_profile_id is None:
                raise TaskFlowServiceError(
                    error_code="task_flow_profile_required",
                    reason="flow_id maintenance scans require profile_id",
                )
            if normalized_flow_id is not None:
                flow = await _require_flow(
                    repo,
                    profile_id=cast(str, normalized_profile_id),
                    flow_id=normalized_flow_id,
                )
                flows = [flow] if flow.status == "active" else []
            else:
                flows = await repo.list_active_flows(
                    profile_id=normalized_profile_id,
                    limit=normalized_limit,
                )
            now_utc = datetime.now(timezone.utc)
            checked: list[TaskKnowledgeMaintenanceFlowMetadata] = []
            created_count = 0
            woken_count = 0
            skipped_count = 0
            for flow in flows[:normalized_limit]:
                health = await _build_knowledge_maintenance_health(repo=repo, flow=flow)
                if not health.reasons:
                    skipped_count += 1
                    checked.append(
                        TaskKnowledgeMaintenanceFlowMetadata(
                            profile_id=flow.profile_id,
                            flow_id=flow.id,
                            flow_title=flow.title,
                            health_status=health.health_status,
                            reasons=health.reasons,
                            missing_flow_document_keys=health.missing_flow_document_keys,
                            unconfirmed_flow_document_keys=health.unconfirmed_flow_document_keys,
                            open_blocked_task_count=health.open_blocked_task_count,
                            open_review_task_count=health.open_review_task_count,
                            action="healthy",
                        )
                    )
                    continue
                source_ref = _knowledge_maintenance_source_ref(flow.id)
                existing_rows = await repo.list_tasks_by_source(
                    profile_id=flow.profile_id,
                    source_type=_KNOWLEDGE_MAINTENANCE_SOURCE_TYPE,
                    source_ref=source_ref,
                    limit=10,
                )
                open_existing = next(
                    (row for row in existing_rows if row.status not in _TASK_TERMINAL_STATUSES),
                    None,
                )
                if open_existing is not None:
                    await _record_task_wake_requested(
                        repo=repo,
                        task=open_existing,
                        reason_code="knowledge_maintenance_required",
                        actor_type=normalized_actor_type,
                        actor_ref=normalized_actor_ref,
                        message="Project knowledge maintenance is still required.",
                        details={
                            "flow_id": flow.id,
                            "flow_title": flow.title,
                            "health_status": health.health_status,
                            "reasons": list(health.reasons),
                            "source_type": _KNOWLEDGE_MAINTENANCE_SOURCE_TYPE,
                            "source_ref": source_ref,
                        },
                    )
                    woken_count += 1
                    checked.append(
                        TaskKnowledgeMaintenanceFlowMetadata(
                            profile_id=flow.profile_id,
                            flow_id=flow.id,
                            flow_title=flow.title,
                            health_status=health.health_status,
                            reasons=health.reasons,
                            missing_flow_document_keys=health.missing_flow_document_keys,
                            unconfirmed_flow_document_keys=health.unconfirmed_flow_document_keys,
                            open_blocked_task_count=health.open_blocked_task_count,
                            open_review_task_count=health.open_review_task_count,
                            task=await _build_task_metadata(
                                repo,
                                open_existing,
                                settings=self._settings,
                            ),
                            action="woken",
                        )
                    )
                    continue
                cooldown_sec = max(
                    1,
                    int(settings.taskflow_knowledge_maintenance_terminal_cooldown_sec),
                )
                cooldown_started_at = now_utc - timedelta(seconds=cooldown_sec)
                recent_terminal = next(
                    (
                        row
                        for row in existing_rows
                        if row.status in _TASK_TERMINAL_STATUSES
                        and _task_recent_activity_at(row) >= cooldown_started_at
                    ),
                    None,
                )
                if recent_terminal is not None:
                    skipped_count += 1
                    checked.append(
                        TaskKnowledgeMaintenanceFlowMetadata(
                            profile_id=flow.profile_id,
                            flow_id=flow.id,
                            flow_title=flow.title,
                            health_status=health.health_status,
                            reasons=health.reasons,
                            missing_flow_document_keys=health.missing_flow_document_keys,
                            unconfirmed_flow_document_keys=health.unconfirmed_flow_document_keys,
                            open_blocked_task_count=health.open_blocked_task_count,
                            open_review_task_count=health.open_review_task_count,
                            task=await _build_task_metadata(
                                repo,
                                recent_terminal,
                                settings=self._settings,
                            ),
                            action="cooldown",
                        )
                    )
                    continue
                try:
                    root_employee_id = await _resolve_default_employee_owner_ref(
                        settings=settings,
                        profile_id=flow.profile_id,
                    )
                except TaskFlowServiceError as exc:
                    skipped_count += 1
                    checked.append(
                        TaskKnowledgeMaintenanceFlowMetadata(
                            profile_id=flow.profile_id,
                            flow_id=flow.id,
                            flow_title=flow.title,
                            health_status="unroutable",
                            reasons=(*health.reasons, exc.error_code),
                            missing_flow_document_keys=health.missing_flow_document_keys,
                            unconfirmed_flow_document_keys=health.unconfirmed_flow_document_keys,
                            open_blocked_task_count=health.open_blocked_task_count,
                            open_review_task_count=health.open_review_task_count,
                            action="unroutable",
                        )
                    )
                    continue
                labels = _normalize_labels(_KNOWLEDGE_MAINTENANCE_LABELS)
                row = await repo.create_task(
                    task_id=_new_identifier("task"),
                    profile_id=flow.profile_id,
                    flow_id=flow.id,
                    title=f"Maintain project knowledge for {flow.title}"[:255],
                    description=_build_knowledge_maintenance_description(
                        flow=flow,
                        health=health,
                    ),
                    status="todo",
                    priority=85,
                    due_at=None,
                    ready_at=now_utc,
                    owner_type=EMPLOYEE_OWNER_TYPE,
                    owner_ref=root_employee_id,
                    reviewer_type=None,
                    reviewer_ref=None,
                    source_type=_KNOWLEDGE_MAINTENANCE_SOURCE_TYPE,
                    source_ref=source_ref,
                    created_by_type=normalized_actor_type,
                    created_by_ref=normalized_actor_ref,
                    labels_json=json.dumps(labels),
                    requires_review=False,
                    blocked_reason_code=None,
                    blocked_reason_text=None,
                )
                await record_task_event(
                    repo=repo,
                    task_id=row.id,
                    event_type="created",
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                    to_status=row.status,
                    details={
                        "flow_id": flow.id,
                        "flow_title": flow.title,
                        "owner_type": row.owner_type,
                        "owner_ref": row.owner_ref,
                        "priority": row.priority,
                        "labels": list(labels),
                        "source_type": row.source_type,
                        "source_ref": row.source_ref,
                        "health_status": health.health_status,
                        "reasons": list(health.reasons),
                    },
                )
                await _record_task_wake_requested(
                    repo=repo,
                    task=row,
                    reason_code="knowledge_maintenance_required",
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                    message=(
                        "Project knowledge maintenance task created for root employee "
                        "intake and validation."
                    ),
                    details={
                        "flow_id": flow.id,
                        "flow_title": flow.title,
                        "health_status": health.health_status,
                        "reasons": list(health.reasons),
                    },
                )
                created_count += 1
                checked.append(
                    TaskKnowledgeMaintenanceFlowMetadata(
                        profile_id=flow.profile_id,
                        flow_id=flow.id,
                        flow_title=flow.title,
                        health_status=health.health_status,
                        reasons=health.reasons,
                        missing_flow_document_keys=health.missing_flow_document_keys,
                        unconfirmed_flow_document_keys=health.unconfirmed_flow_document_keys,
                        open_blocked_task_count=health.open_blocked_task_count,
                        open_review_task_count=health.open_review_task_count,
                        task=await _build_task_metadata(repo, row, settings=self._settings),
                        action="created",
                    )
                )
            return TaskKnowledgeMaintenanceSweepMetadata(
                generated_at=now_utc,
                profile_id=normalized_profile_id or "*",
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                checked_flow_count=len(checked),
                created_task_count=created_count,
                woken_task_count=woken_count,
                skipped_flow_count=skipped_count,
                flows=tuple(checked),
            )

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
        _validate_actor_pair(
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
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
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Adding a task comment requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_task_actor_can_manage(
                row=task,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_actor_refs_exist(
                repo,
                settings=self._settings,
                profile_id=profile_id,
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
                settings=self._settings,
                profile_id=profile_id,
                task_id=task.id,
                task_run_id=task_run_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                message=normalized_message,
                comment_type=normalized_comment_type,
            )
            responsible_employee = _task_responsible_employee(task)
            if (
                responsible_employee is not None
                and task.status not in {"completed", "failed", "cancelled"}
                and not (
                    normalized_actor_type == EMPLOYEE_OWNER_TYPE
                    and normalized_actor_ref == responsible_employee[1]
                )
            ):
                await _record_task_wake_requested(
                    repo=repo,
                    task=task,
                    reason_code="comment_added",
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                    message="Comment added for the responsible employee.",
                    details={"comment_id": row.id},
                )
                if task.status == "blocked":
                    await _record_manager_escalation_if_needed(
                        repo=repo,
                        settings=self._settings,
                        task=task,
                        reason_code=task.blocked_reason_code,
                        reason_text=task.blocked_reason_text,
                        actor_type=normalized_actor_type,
                        actor_ref=normalized_actor_ref,
                    )
            return _to_task_comment_metadata(row)

        return await self._with_repo(_op)

    async def build_employee_inbox(
        self,
        *,
        profile_id: str,
        owner_type: str,
        owner_ref: str,
        task_limit: int = 10,
        event_limit: int = 10,
    ) -> EmployeeTaskInboxMetadata:
        """Build an inbox for one employee from assignments and mentions."""

        normalized_owner_type = normalize_task_owner_type(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        _validate_owner_pair(
            owner_type=normalized_owner_type,
            owner_ref=normalized_owner_ref,
            allow_missing=False,
        )
        if normalized_owner_type is None or normalized_owner_ref is None:
            raise TaskFlowServiceError(
                error_code="invalid_owner_ref",
                reason="Employee inbox owner is required",
            )
        if not is_employee_executor_owner_type(normalized_owner_type):
            raise TaskFlowServiceError(
                error_code="task_employee_inbox_owner_required",
                reason="Task Flow inbox requires an employee owner",
            )

        async def _op(repo: TaskFlowRepository) -> EmployeeTaskInboxMetadata:
            await _ensure_profile_exists(repo, profile_id)
            await _ensure_actor_refs_exist(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                reviewer_type=None,
                reviewer_ref=None,
            )
            actionable = await repo.list_employee_inbox_tasks(
                profile_id=profile_id,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                limit=max(task_limit, 1),
            )
            recent_events = await repo.list_task_feed_events_for_owner(
                profile_id=profile_id,
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                limit=event_limit,
            )
            title_by_task_id: dict[str, str] = {}
            for event in recent_events:
                if event.task_id in title_by_task_id:
                    continue
                row = await repo.get_task(profile_id=profile_id, task_id=event.task_id)
                title_by_task_id[event.task_id] = row.title if row is not None else event.task_id
            return EmployeeTaskInboxMetadata(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                total_count=len(actionable),
                todo_count=sum(1 for row in actionable if row.status == "todo"),
                blocked_count=sum(1 for row in actionable if row.status == "blocked"),
                review_count=sum(1 for row in actionable if row.status == "review"),
                running_count=sum(1 for row in actionable if row.status in {"claimed", "running"}),
                mention_event_count=sum(
                    1 for row in recent_events if row.event_type == "mention_created"
                ),
                tasks=tuple(
                    await _build_task_metadata_many(
                        repo,
                        actionable,
                        settings=self._settings,
                    )
                ),
                recent_events=tuple(
                    _to_employee_task_inbox_event_metadata(
                        row,
                        task_title=title_by_task_id.get(row.task_id, row.task_id),
                    )
                    for row in recent_events
                ),
            )

        return await self._with_repo(_op)

    async def build_task_context(
        self,
        *,
        profile_id: str,
        task_id: str,
        event_limit: int = 20,
        comment_limit: int = 10,
    ) -> TaskContextMetadata:
        """Build an AFKBOT Knowledge Packet context bundle for one task execution."""

        normalized_task_id = _normalize_required_text(task_id, field_name="task_id")

        async def _op(repo: TaskFlowRepository) -> TaskContextMetadata:
            task = await _require_task(repo, profile_id=profile_id, task_id=normalized_task_id)
            flow: TaskFlow | None = None
            flow_documents: list[TaskDocument] = []
            if task.flow_id is not None:
                flow = await repo.get_flow(profile_id=profile_id, flow_id=task.flow_id)
                if flow is not None:
                    await _ensure_default_flow_documents(
                        repo=repo,
                        flow=flow,
                        actor_type=flow.created_by_type,
                        actor_ref=flow.created_by_ref,
                    )
                    flow_documents = await repo.list_task_documents(
                        profile_id=profile_id,
                        scope_type=_TASK_DOCUMENT_SCOPE_FLOW,
                        scope_id=flow.id,
                    )
                    flow_documents = list(select_canonical_flow_documents(flow_documents))
            task_documents = await repo.list_task_documents(
                profile_id=profile_id,
                scope_type=_TASK_DOCUMENT_SCOPE_TASK,
                scope_id=task.id,
            )
            task_documents = list(select_task_working_documents(task_documents))
            dependencies = await repo.list_dependencies(task_id=task.id)
            dependency_rows: list[Task] = []
            for edge in dependencies:
                dependency = await repo.get_task(
                    profile_id=profile_id,
                    task_id=edge.depends_on_task_id,
                )
                if dependency is not None:
                    dependency_rows.append(dependency)
            dependents = await repo.list_dependents(depends_on_task_id=task.id)
            dependent_rows: list[Task] = []
            for edge in dependents:
                dependent = await repo.get_task(profile_id=profile_id, task_id=edge.task_id)
                if dependent is not None:
                    dependent_rows.append(dependent)
            delegated_rows = await repo.list_tasks_by_source(
                profile_id=profile_id,
                source_type="task_delegation",
                source_ref=task.id,
                limit=20,
            )
            event_rows = await repo.list_task_events(task_id=task.id, limit=max(event_limit, 1))
            comment_rows = [
                row
                for row in event_rows
                if str(row.event_type or "").strip() == _TASK_COMMENT_EVENT_TYPE
            ][: max(comment_limit, 1)]
            knowledge_packet = build_knowledge_packet(
                profile_id=profile_id,
                flow_id=task.flow_id,
                task_id=task.id,
                flow_documents=flow_documents,
                task_documents=task_documents,
            )
            return TaskContextMetadata(
                generated_at=datetime.now(timezone.utc),
                task=await _build_task_metadata(repo, task, settings=self._settings),
                flow=_to_flow_metadata(flow) if flow is not None else None,
                knowledge_packet=_to_knowledge_packet_metadata(knowledge_packet),
                flow_documents=tuple(_to_task_document_metadata(row) for row in flow_documents),
                task_documents=tuple(_to_task_document_metadata(row) for row in task_documents),
                dependencies=tuple(_to_dependency_metadata(row) for row in dependencies),
                dependency_tasks=tuple(
                    await _build_task_metadata_many(
                        repo,
                        dependency_rows,
                        settings=self._settings,
                    )
                ),
                dependents=tuple(_to_dependency_metadata(row) for row in dependents),
                dependent_tasks=tuple(
                    await _build_task_metadata_many(
                        repo,
                        dependent_rows,
                        settings=self._settings,
                    )
                ),
                delegated_tasks=tuple(
                    await _build_task_metadata_many(
                        repo,
                        delegated_rows,
                        settings=self._settings,
                    )
                ),
                recent_comments=tuple(_to_task_comment_metadata(row) for row in comment_rows),
                recent_events=tuple(_to_task_event_metadata(row) for row in event_rows),
            )

        return await self._with_repo(_op)

    async def list_review_tasks(
        self,
        *,
        profile_id: str,
        actor_type: str | None = None,
        actor_ref: str | None = None,
        flow_id: str | None = None,
        labels: Sequence[str] = (),
        limit: int | None = None,
    ) -> list[TaskMetadata]:
        """List review-queue tasks for one reviewer/actor inbox, or all reviewers."""

        normalized_actor_type = normalize_task_owner_type(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_actor_pair(
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
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
                statuses=("review", "claimed", "running"),
                flow_id=normalized_flow_id,
            )
            filtered_rows = [
                row
                for row in rows
                if _task_is_review_actionable(row)
                and _task_matches_required_labels(row=row, labels=normalized_labels)
                and (
                    normalized_actor_type is None
                    or normalized_actor_ref is None
                    or _task_matches_review_inbox(
                        row=row,
                        actor_type=normalized_actor_type,
                        actor_ref=normalized_actor_ref,
                    )
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

        normalized_actor_type = normalize_task_owner_type(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_actor_pair(
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
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
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_review_actor_required",
                reason="Review approval requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            if not _task_is_review_actionable(row):
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
            await ensure_manager_intake_transition_allowed(
                repo=repo,
                settings=self._settings,
                profile_id=profile_id,
                task_id=row.id,
                source_type=row.source_type,
                owner_type=row.owner_type,
                owner_ref=row.owner_ref,
                labels=_decode_labels(row.labels_json),
                target_status="completed",
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
        normalized_reason_code = _canonical_manager_escalation_reason_code(
            reason_code=normalized_reason_code,
            reason_text=normalized_reason_text,
        )
        normalized_actor_type = normalize_task_owner_type(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        normalized_actor_session_id = _normalize_optional_text(actor_session_id)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_actor_pair(
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
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
        normalized_owner_type = normalize_task_owner_type(owner_type)
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
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_review_actor_required",
                reason="Requesting review changes requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            if not _task_is_review_actionable(row):
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
            route_changes_to_reviewer = (
                normalized_owner_type is None
                and normalized_owner_ref is None
                and normalized_actor_type is not None
                and normalized_actor_ref is not None
                and is_employee_executor_owner_type(normalized_actor_type)
                and _task_matches_review_inbox(
                    row=row,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                )
            )
            effective_owner_type = normalized_owner_type or (
                normalized_actor_type if route_changes_to_reviewer else row.owner_type
            )
            effective_owner_ref = normalized_owner_ref or (
                normalized_actor_ref if route_changes_to_reviewer else row.owner_ref
            )
            await _ensure_actor_refs_exist(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
                reviewer_type=row.reviewer_type,
                reviewer_ref=row.reviewer_ref,
            )
            await _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
            )
            manager_escalation_blocker = _requires_manager_escalation_blocker(
                reason_code=normalized_reason_code,
                reason_text=normalized_reason_text,
            )
            updated = await repo.update_task(
                profile_id=profile_id,
                task_id=row.id,
                status="blocked",
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
                ready_at=(
                    datetime.now(timezone.utc)
                    if is_employee_executor_owner_type(effective_owner_type)
                    and normalized_reason_code != "dependency_wait"
                    and not manager_escalation_blocker
                    else None
                ),
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
            if (
                is_employee_executor_owner_type(updated.owner_type)
                and not manager_escalation_blocker
            ):
                await _record_task_wake_requested(
                    repo=repo,
                    task=updated,
                    reason_code=normalized_reason_code,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                    message="Review changes requested; employee owner should resume this task.",
                )
            if is_employee_executor_owner_type(updated.owner_type):
                await _record_manager_escalation_if_needed(
                    repo=repo,
                    settings=self._settings,
                    task=updated,
                    reason_code=normalized_reason_code,
                    reason_text=normalized_reason_text,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
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
            _validate_actor_pair(
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
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
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Adding a dependency requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_task_actor_can_manage(
                row=task,
                settings=self._settings,
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
            _validate_actor_pair(
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
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
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Removing a dependency requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_task_actor_can_manage(
                row=task,
                settings=self._settings,
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
        delegated_owner_type: str = EMPLOYEE_OWNER_TYPE,
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
        """Create one delegated employee-owned task and optionally block the source task on it."""

        normalized_source_task_id = _normalize_required_text(
            source_task_id, field_name="source_task_id"
        )
        normalized_delegate_owner_type = normalize_task_owner_type(delegated_owner_type)
        if normalized_delegate_owner_type is None:
            raise TaskFlowServiceError(
                error_code="invalid_delegated_owner_type",
                reason="delegated_owner_type is required",
            )
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
        _validate_actor_pair(
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            allow_missing=False,
        )
        _validate_owner_pair(
            owner_type=normalized_delegate_owner_type,
            owner_ref=normalized_delegate_owner_ref,
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
            source_task = await _require_task(
                repo, profile_id=profile_id, task_id=normalized_source_task_id
            )
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Delegating a task requires an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            if source_task.status in {"completed", "failed", "cancelled"}:
                raise TaskFlowServiceError(
                    error_code="task_delegate_invalid_state",
                    reason="Terminal task cannot delegate new work",
                )
            await _ensure_task_actor_can_manage(
                row=source_task,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            await _ensure_actor_refs_exist(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                owner_type=normalized_actor_type,
                owner_ref=normalized_actor_ref,
                reviewer_type=None,
                reviewer_ref=None,
            )
            await _ensure_actor_refs_exist(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                owner_type=normalized_delegate_owner_type,
                owner_ref=normalized_delegate_owner_ref,
                reviewer_type=None,
                reviewer_ref=None,
            )
            await _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                owner_type=normalized_delegate_owner_type,
                owner_ref=normalized_delegate_owner_ref,
            )
            delegated_flow_id = (
                normalized_flow_id if normalized_flow_id is not None else source_task.flow_id
            )
            if delegated_flow_id is not None:
                flow = await repo.get_flow(profile_id=profile_id, flow_id=delegated_flow_id)
                if flow is None:
                    raise TaskFlowServiceError(
                        error_code="task_flow_not_found",
                        reason="Task flow not found",
                    )
            delegated_title = (
                normalized_title or f"{source_task.title} [{normalized_delegate_owner_ref}]"
            )
            delegated_priority = priority if priority is not None else source_task.priority
            delegated_due_at = due_at if due_at is not None else source_task.due_at
            delegated_labels = (
                normalized_labels
                if normalized_labels is not None
                else _delegated_task_labels(source_task)
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
                owner_type=normalized_delegate_owner_type,
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
                f"Delegated to {normalized_delegate_owner_type}:{normalized_delegate_owner_ref} "
                f"as task {delegated_row.id}."
            )
            await _append_task_comment_event(
                repo=repo,
                settings=self._settings,
                profile_id=profile_id,
                task_id=source_task.id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                message=parent_comment,
                comment_type="delegation",
            )
            await _append_task_comment_event(
                repo=repo,
                settings=self._settings,
                profile_id=profile_id,
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
                refreshed_source = await repo.get_task(
                    profile_id=profile_id, task_id=source_task.id
                )
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
                raise TaskFlowServiceError(
                    error_code="task_run_not_found", reason="Task run not found"
                )
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
        normalized_owner_type = normalize_task_owner_type(owner_type)
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
        reviewer_type: str | None | object = _TASK_FIELD_UNSET,
        reviewer_ref: str | None | object = _TASK_FIELD_UNSET,
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
        if (
            normalized_session_profile_id is not _TASK_FIELD_UNSET
            and normalized_session_id is _TASK_FIELD_UNSET
        ):
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
        manager_escalation_blocker_update = (
            normalized_blocked_reason_code is not _TASK_FIELD_UNSET
            or normalized_blocked_reason_text is not _TASK_FIELD_UNSET
        ) and _requires_manager_escalation_blocker(
            reason_code=(
                cast(str | None, normalized_blocked_reason_code)
                if normalized_blocked_reason_code is not _TASK_FIELD_UNSET
                else None
            ),
            reason_text=(
                cast(str | None, normalized_blocked_reason_text)
                if normalized_blocked_reason_text is not _TASK_FIELD_UNSET
                else None
            ),
        )
        if (
            (
                normalized_blocked_reason_code is not _TASK_FIELD_UNSET
                or normalized_blocked_reason_text is not _TASK_FIELD_UNSET
            )
            and manager_escalation_blocker_update
            and ready_at not in {_TASK_FIELD_UNSET, None}
        ):
            raise TaskFlowServiceError(
                error_code="task_manager_escalation_ready_at_conflict",
                reason="manager escalation blockers cannot schedule a timed revisit",
            )
        if manager_escalation_blocker_update and (
            normalized_blocked_reason_code is _TASK_FIELD_UNSET
            or cast(str | None, normalized_blocked_reason_code)
            not in _MANAGER_ESCALATION_BLOCKER_CODES
        ):
            normalized_blocked_reason_code = "manager_reassignment_required"
        normalized_ready_at = (
            None
            if manager_escalation_blocker_update and ready_at is _TASK_FIELD_UNSET
            else ready_at
        )
        normalized_owner_type = normalize_task_owner_type(owner_type)
        normalized_owner_ref = _normalize_optional_text(owner_ref)
        if normalized_owner_type is not None or normalized_owner_ref is not None:
            _validate_owner_pair(
                owner_type=normalized_owner_type,
                owner_ref=normalized_owner_ref,
                allow_missing=False,
            )
        normalized_reviewer_type: str | None | object = _TASK_FIELD_UNSET
        if reviewer_type is not _TASK_FIELD_UNSET:
            normalized_reviewer_type = normalize_task_owner_type(cast(str | None, reviewer_type))
        normalized_reviewer_ref: str | None | object = _TASK_FIELD_UNSET
        if reviewer_ref is not _TASK_FIELD_UNSET:
            normalized_reviewer_ref = _normalize_optional_text(cast(str | None, reviewer_ref))
        if (
            normalized_reviewer_type is not _TASK_FIELD_UNSET
            or normalized_reviewer_ref is not _TASK_FIELD_UNSET
        ):
            _validate_owner_pair(
                owner_type=cast(str | None, normalized_reviewer_type)
                if normalized_reviewer_type is not _TASK_FIELD_UNSET
                else None,
                owner_ref=cast(str | None, normalized_reviewer_ref)
                if normalized_reviewer_ref is not _TASK_FIELD_UNSET
                else None,
                allow_missing=True,
            )
        normalized_actor_type = _normalize_optional_text(actor_type)
        normalized_actor_ref = _normalize_optional_text(actor_ref)
        if normalized_actor_type is not None or normalized_actor_ref is not None:
            _validate_actor_pair(
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
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

        async def _op(repo: TaskFlowRepository) -> tuple[TaskMetadata, bool]:
            await _ensure_profile_exists(repo, profile_id)
            await _ensure_public_ai_principal_session(
                repo,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                actor_session_id=normalized_actor_session_id,
                error_code="task_actor_required",
                reason="Task updates require an explicit actor identity",
            )
            await _ensure_principal_exists(
                repo,
                settings=self._settings,
                profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            current_row = await repo.get_task(profile_id=profile_id, task_id=task_id)
            if current_row is None:
                raise TaskFlowServiceError(error_code="task_not_found", reason="Task not found")
            repo_blocked_reason_code = normalized_blocked_reason_code
            repo_blocked_reason_text = normalized_blocked_reason_text
            repo_ready_at = normalized_ready_at
            await _ensure_task_actor_can_manage(
                row=current_row,
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
            )
            before = _snapshot_task(current_row)
            requested_session_id = normalized_session_id
            requested_session_profile_id = normalized_session_profile_id
            owner_changed = (
                normalized_owner_type is not None
                and normalized_owner_type != current_row.owner_type
            ) or (
                normalized_owner_ref is not None and normalized_owner_ref != current_row.owner_ref
            )
            effective_owner_type = normalized_owner_type or current_row.owner_type
            effective_owner_ref = normalized_owner_ref or current_row.owner_ref
            effective_reviewer_type = (
                current_row.reviewer_type
                if normalized_reviewer_type is _TASK_FIELD_UNSET
                else cast(str | None, normalized_reviewer_type)
            )
            effective_reviewer_ref = (
                current_row.reviewer_ref
                if normalized_reviewer_ref is _TASK_FIELD_UNSET
                else cast(str | None, normalized_reviewer_ref)
            )
            if (
                normalized_reviewer_type is _TASK_FIELD_UNSET
                and normalized_reviewer_ref is _TASK_FIELD_UNSET
            ):
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
                settings=self._settings,
                profile_id=profile_id,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
                reviewer_type=effective_reviewer_type,
                reviewer_ref=effective_reviewer_ref,
            )
            await _ensure_ai_owner_assignment_allowed(
                settings=self._settings,
                task_profile_id=profile_id,
                actor_type=normalized_actor_type,
                actor_ref=normalized_actor_ref,
                owner_type=effective_owner_type,
                owner_ref=effective_owner_ref,
            )
            effective_status_after_update = effective_status or current_row.status
            existing_manager_escalation_blocker = (
                current_row.status == "blocked"
                and _requires_manager_escalation_blocker(
                    reason_code=current_row.blocked_reason_code,
                    reason_text=current_row.blocked_reason_text,
                )
            )
            preserving_existing_manager_blocker = (
                existing_manager_escalation_blocker
                and effective_status_after_update == "blocked"
                and normalized_blocked_reason_code is _TASK_FIELD_UNSET
                and normalized_blocked_reason_text is _TASK_FIELD_UNSET
            )
            if preserving_existing_manager_blocker:
                if ready_at not in {_TASK_FIELD_UNSET, None}:
                    raise TaskFlowServiceError(
                        error_code="task_manager_escalation_ready_at_conflict",
                        reason="manager escalation blockers cannot schedule a timed revisit",
                    )
                repo_blocked_reason_code = current_row.blocked_reason_code
                repo_blocked_reason_text = current_row.blocked_reason_text
                repo_ready_at = None
            elif (
                existing_manager_escalation_blocker
                and effective_status_after_update != "blocked"
                and not await _task_actor_has_manager_scope(
                    row=current_row,
                    settings=self._settings,
                    task_profile_id=profile_id,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                )
            ):
                raise TaskFlowServiceError(
                    error_code="task_manager_escalation_resolution_forbidden",
                    reason=(
                        "Only a human operator or the responsible manager can resolve "
                        "a manager escalation blocker"
                    ),
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
            if normalized_actor_type == "automation" and effective_status_after_update in {
                "claimed",
                "running",
            }:
                raise TaskFlowServiceError(
                    error_code="task_session_binding_forbidden",
                    reason="Automation actors cannot move tasks into claimed/running state",
                )
            if (
                is_employee_executor_owner_type(effective_owner_type)
                and effective_status_after_update in {"claimed", "running"}
                and not effective_session_id_text
            ):
                raise TaskFlowServiceError(
                    error_code="task_session_required",
                    reason="Active AI tasks require a bound session_id",
                )
            if (
                is_employee_executor_owner_type(effective_owner_type)
                and effective_status in {"claimed", "running"}
                and await repo.has_active_ai_task(
                    profile_id=profile_id,
                    owner_type=effective_owner_type,
                    owner_ref=effective_owner_ref,
                    exclude_task_id=current_row.id,
                )
            ):
                raise TaskFlowServiceError(
                    error_code="task_owner_active_conflict",
                    reason="Employee owner already has another active task",
                )
            if effective_status_after_update in {"completed", "review"}:
                await ensure_manager_intake_transition_allowed(
                    repo=repo,
                    settings=self._settings,
                    profile_id=profile_id,
                    task_id=current_row.id,
                    source_type=current_row.source_type,
                    owner_type=current_row.owner_type,
                    owner_ref=current_row.owner_ref,
                    labels=tuple(_decode_labels(current_row.labels_json)),
                    target_status=effective_status_after_update,
                )
                await ensure_manager_intake_transition_allowed(
                    repo=repo,
                    settings=self._settings,
                    profile_id=profile_id,
                    task_id=current_row.id,
                    source_type=current_row.source_type,
                    owner_type=effective_owner_type,
                    owner_ref=effective_owner_ref,
                    labels=(
                        _normalize_labels(labels)
                        if labels is not None
                        else tuple(_decode_labels(current_row.labels_json))
                    ),
                    target_status=effective_status_after_update,
                )
            if effective_status_after_update == "completed":
                await _ensure_employee_completion_actor_allowed(
                    row=current_row,
                    settings=self._settings,
                    task_profile_id=profile_id,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
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
                _ensure_public_session_binding_allowed(
                    settings=self._settings,
                    task_profile_id=profile_id,
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
                    ready_at=(
                        repo_ready_at
                        if repo_ready_at is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                    owner_type=normalized_owner_type,
                    owner_ref=normalized_owner_ref,
                    reviewer_type=(
                        normalized_reviewer_type
                        if normalized_reviewer_type is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                    reviewer_ref=(
                        normalized_reviewer_ref
                        if normalized_reviewer_ref is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                    requires_review=requires_review,
                    labels_json=(
                        json.dumps(_normalize_labels(labels)) if labels is not None else None
                    ),
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
                        repo_blocked_reason_code
                        if repo_blocked_reason_code is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                    blocked_reason_text=(
                        repo_blocked_reason_text
                        if repo_blocked_reason_text is not _TASK_FIELD_UNSET
                        else _REPO_FIELD_UNSET
                    ),
                )
            except IntegrityError as exc:
                if _is_active_ai_owner_integrity_error(exc):
                    raise TaskFlowServiceError(
                        error_code="task_owner_active_conflict",
                        reason="Employee owner already has another active task",
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
            if (
                _task_responsible_employee(row) is not None
                and row.status in {"todo", "blocked", "review"}
                and row.ready_at is not None
                and (before.ready_at != row.ready_at or before.status != row.status)
            ):
                await _record_task_wake_requested(
                    repo=repo,
                    task=row,
                    reason_code=str(row.blocked_reason_code or row.status or "task_ready"),
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                    message="Task has a scheduled AI wake.",
                )
            if is_employee_executor_owner_type(row.owner_type) and row.status == "blocked":
                await _record_manager_escalation_if_needed(
                    repo=repo,
                    settings=self._settings,
                    task=row,
                    reason_code=row.blocked_reason_code,
                    reason_text=row.blocked_reason_text,
                    actor_type=normalized_actor_type,
                    actor_ref=normalized_actor_ref,
                )
            refresh_schema_invariants = (
                is_employee_executor_owner_type(before.owner_type)
                and before.status in {"claimed", "running"}
            ) or (
                is_employee_executor_owner_type(row.owner_type)
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
        normalized_reason_code = _canonical_manager_escalation_reason_code(
            reason_code=normalized_reason_code,
            reason_text=normalized_reason_text,
        )
        if normalized_reason_code == "dependency_wait" and ready_at not in {
            _TASK_FIELD_UNSET,
            None,
        }:
            raise TaskFlowServiceError(
                error_code="task_dependency_wait_ready_at_conflict",
                reason="dependency_wait blockers cannot schedule a timed revisit",
            )
        if _requires_manager_escalation_blocker(
            reason_code=normalized_reason_code,
            reason_text=normalized_reason_text,
        ) and ready_at not in {
            _TASK_FIELD_UNSET,
            None,
        }:
            raise TaskFlowServiceError(
                error_code="task_manager_escalation_ready_at_conflict",
                reason="manager escalation blockers cannot schedule a timed revisit",
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

    async def _with_repo(
        self,
        op: Callable[[TaskFlowRepository], Awaitable[TValue]],
    ) -> TValue:
        async with session_write_scope(self._session_factory) as session:
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
            await ensure_task_runtime_schema(engine)
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


async def _require_flow(
    repo: TaskFlowRepository,
    *,
    profile_id: str,
    flow_id: str,
) -> TaskFlow:
    """Load one flow or raise a structured service error."""

    await _ensure_profile_exists(repo, profile_id)
    row = await repo.get_flow(profile_id=profile_id, flow_id=flow_id)
    if row is None:
        raise TaskFlowServiceError(error_code="task_flow_not_found", reason="Task flow not found")
    return row


async def _ensure_document_scope_exists(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    scope_type: str,
    scope_id: str,
) -> None:
    """Validate a document scope before writing document state."""

    if scope_type == _TASK_DOCUMENT_SCOPE_FLOW:
        await _require_flow(repo, profile_id=profile_id, flow_id=scope_id)
        return
    if scope_type == _TASK_DOCUMENT_SCOPE_TASK:
        await _require_task(repo, profile_id=profile_id, task_id=scope_id)
        return
    raise TaskFlowServiceError(
        error_code="invalid_task_document_scope",
        reason=f"Unsupported Task Flow document scope: {scope_type}",
    )


async def _ensure_document_actor_can_manage(
    *,
    repo: TaskFlowRepository,
    settings: Settings | None,
    profile_id: str,
    scope_type: str,
    scope_id: str,
    actor_type: str | None,
    actor_ref: str | None,
) -> None:
    """Apply the same ownership boundary to document writes as task/flow writes."""

    if scope_type == _TASK_DOCUMENT_SCOPE_FLOW:
        await _require_flow(repo, profile_id=profile_id, flow_id=scope_id)
        await _ensure_flow_actor_can_manage(
            settings=settings,
            profile_id=profile_id,
            actor_type=actor_type,
            actor_ref=actor_ref,
        )
        return
    if scope_type == _TASK_DOCUMENT_SCOPE_TASK:
        task = await _require_task(repo, profile_id=profile_id, task_id=scope_id)
        await _ensure_task_actor_can_manage(
            row=task,
            settings=settings,
            task_profile_id=profile_id,
            actor_type=actor_type,
            actor_ref=actor_ref,
        )
        return
    raise TaskFlowServiceError(
        error_code="invalid_task_document_scope",
        reason=f"Unsupported Task Flow document scope: {scope_type}",
    )


async def _ensure_default_flow_documents(
    *,
    repo: TaskFlowRepository,
    flow: TaskFlow,
    actor_type: str,
    actor_ref: str,
) -> None:
    """Create missing default documents for a flow without touching existing revisions."""

    for document_key, title, template in _DEFAULT_FLOW_DOCUMENTS:
        existing = await repo.get_task_document(
            profile_id=flow.profile_id,
            scope_type=_TASK_DOCUMENT_SCOPE_FLOW,
            scope_id=flow.id,
            document_key=document_key,
        )
        if existing is not None:
            continue
        await repo.create_task_document(
            document_id=_new_identifier("doc"),
            profile_id=flow.profile_id,
            scope_type=_TASK_DOCUMENT_SCOPE_FLOW,
            scope_id=flow.id,
            document_key=document_key,
            title=title,
            body=default_flow_document_body(
                flow=flow, document_key=document_key, template=template
            ),
            created_by_type=actor_type,
            created_by_ref=actor_ref,
        )


def _filter_current_contract_documents(rows: Iterable[TaskDocument]) -> tuple[TaskDocument, ...]:
    """Return all documents exposed by the current Task Flow document contract."""

    allowed: list[TaskDocument] = []
    for row in rows:
        if (
            row.scope_type == _TASK_DOCUMENT_SCOPE_FLOW
            and row.document_key in CANONICAL_FLOW_DOCUMENT_KEYS
        ):
            allowed.append(row)
        elif (
            row.scope_type == _TASK_DOCUMENT_SCOPE_TASK
            and row.document_key in TASK_WORKING_DOCUMENT_KEYS
        ):
            allowed.append(row)
    return tuple(allowed)


async def _ensure_actor_refs_exist(
    repo: TaskFlowRepository,
    *,
    settings: Settings | None,
    profile_id: str | None = None,
    owner_type: str | None,
    owner_ref: str | None,
    reviewer_type: str | None,
    reviewer_ref: str | None,
) -> None:
    """Validate task principal references for owners/reviewers before persistence."""

    await _ensure_task_principal_ref_exists(
        repo,
        settings=settings,
        profile_id=profile_id,
        principal_type=owner_type,
        principal_ref=owner_ref,
        invalid_error_code="invalid_owner_ref",
    )
    await _ensure_task_principal_ref_exists(
        repo,
        settings=settings,
        profile_id=profile_id,
        principal_type=reviewer_type,
        principal_ref=reviewer_ref,
        invalid_error_code="invalid_reviewer_ref",
    )


async def _resolve_default_employee_owner_ref(
    *,
    settings: Settings | None,
    profile_id: str,
) -> str:
    """Return the single active root employee for a profile or require explicit owner."""

    try:
        org_chart = await EmployeeService(settings or get_settings()).build_org_chart(
            profile_id=profile_id
        )
    except EmployeeServiceError as exc:
        raise TaskFlowServiceError(
            error_code="task_employee_owner_required",
            reason="Task owner is required because this profile has no valid employee root",
        ) from exc
    root_ids = tuple(
        employee_id
        for employee_id in org_chart.root_employee_ids
        if org_chart.employees[employee_id].status == "active"
    )
    if len(root_ids) == 1:
        return root_ids[0]
    raise TaskFlowServiceError(
        error_code="task_employee_owner_required",
        reason="Task owner is required when the profile has zero or multiple root employees",
    )


async def _ensure_principal_exists(
    repo: TaskFlowRepository,
    *,
    settings: Settings | None,
    profile_id: str | None = None,
    actor_type: str | None,
    actor_ref: str | None,
) -> None:
    """Validate one actor principal reference when it points at an employee or automation."""

    await _ensure_task_principal_ref_exists(
        repo,
        settings=settings,
        profile_id=profile_id,
        principal_type=actor_type,
        principal_ref=actor_ref,
        invalid_error_code="invalid_actor_ref",
    )
    if actor_type == "automation" and actor_ref is not None:
        parsed = parse_automation_principal_ref(actor_ref)
        if parsed is None:
            raise TaskFlowServiceError(
                error_code="invalid_actor_ref",
                reason="automation actor_ref must match automation:<profile_id>:<automation_id>",
            )
        await _ensure_profile_exists(repo, parsed.profile_id)
        try:
            await ensure_automation_principal_exists(repo._session, actor_ref=actor_ref)
        except AutomationPrincipalValidationError:
            raise TaskFlowServiceError(
                error_code="invalid_actor_ref",
                reason="automation actor_ref must match automation:<profile_id>:<automation_id>",
            )
        except LookupError:
            raise TaskFlowServiceError(
                error_code="automation_not_found",
                reason="Automation principal not found",
            )


async def _ensure_task_principal_ref_exists(
    repo: TaskFlowRepository,
    *,
    settings: Settings | None,
    profile_id: str | None = None,
    principal_type: str | None,
    principal_ref: str | None,
    invalid_error_code: str,
) -> None:
    """Validate one task principal reference when it targets an employee."""

    normalized_type = normalize_task_owner_type(principal_type)
    normalized_ref = _normalize_optional_text(principal_ref)
    if normalized_type == EMPLOYEE_OWNER_TYPE and normalized_ref is not None:
        employee_id = parse_employee_owner_ref(normalized_ref)
        if employee_id is None:
            raise TaskFlowServiceError(
                error_code=invalid_error_code,
                reason="employee ref must match a profile-local employee id",
            )
        if profile_id is None:
            return
        try:
            employee = await EmployeeService(settings or get_settings()).get_employee(
                profile_id=profile_id,
                employee_id=employee_id,
            )
        except EmployeeServiceError as exc:
            raise TaskFlowServiceError(
                error_code=exc.error_code,
                reason=exc.reason,
            ) from exc
        if employee.status != "active":
            raise TaskFlowServiceError(
                error_code=invalid_error_code,
                reason=f"Employee {employee_id} is {employee.status} and cannot be used",
            )
        return
    return


async def _ensure_public_ai_principal_session(
    repo: TaskFlowRepository,
    *,
    settings: Settings | None,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
    actor_session_id: str | None | object,
    error_code: str,
    reason: str,
) -> None:
    """Validate that one public employee actor really owns the supplied live chat session."""

    if settings is None or not bool(settings.taskflow_public_principal_required):
        return
    normalized_actor_type = normalize_task_owner_type(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    normalized_actor_session_id = (
        _normalize_optional_text(cast(str | None, actor_session_id))
        if actor_session_id is not _TASK_FIELD_UNSET
        else None
    )
    if normalized_actor_type != EMPLOYEE_OWNER_TYPE or normalized_actor_ref is None:
        return
    actor_profile_id = task_profile_id
    if normalized_actor_session_id is None:
        return
    session_row = await ChatSessionRepository(repo._session).get(normalized_actor_session_id)
    if session_row is None or session_row.profile_id != actor_profile_id:
        raise TaskFlowServiceError(error_code=error_code, reason=reason)
    if normalized_actor_type == EMPLOYEE_OWNER_TYPE:
        bound_task = await repo.get_task_by_session_binding(
            profile_id=actor_profile_id,
            session_id=normalized_actor_session_id,
            actor_type=EMPLOYEE_OWNER_TYPE,
            actor_ref=normalized_actor_ref or "",
        )
        active_bound_task = await repo.get_active_task_by_session(
            profile_id=actor_profile_id,
            session_id=normalized_actor_session_id,
        )
        if bound_task is None and active_bound_task is not None:
            raise TaskFlowServiceError(error_code=error_code, reason=reason)
    activity_rows = await ChatSessionTurnQueueRepository(repo._session).list_session_activity(
        session_keys=((actor_profile_id, normalized_actor_session_id),),
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
    _validate_actor_pair(
        actor_type=normalized_actor_type,
        actor_ref=normalized_actor_ref,
        allow_missing=False,
    )
    if normalized_actor_type == "human" and normalized_actor_ref != resolve_local_human_ref(
        settings
    ):
        raise TaskFlowServiceError(error_code=error_code, reason=reason)
    if normalized_actor_type == EMPLOYEE_OWNER_TYPE and normalized_actor_session_id is None:
        raise TaskFlowServiceError(error_code=error_code, reason=reason)


def _normalize_required_text(value: str | None, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if normalized:
        return normalized
    raise TaskFlowServiceError(
        error_code=f"invalid_{field_name}", reason=f"{field_name} is required"
    )


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


def _parse_automation_actor_ref(actor_ref: str | None) -> tuple[str, str] | None:
    parsed = parse_automation_principal_ref(actor_ref)
    if parsed is None:
        return None
    return parsed.profile_id, str(parsed.automation_id)


def _normalize_status(status: str) -> str:
    normalized = _normalize_required_text(status, field_name="status").lower()
    if normalized not in _VALID_TASK_STATUSES:
        raise TaskFlowServiceError(
            error_code="invalid_status", reason=f"Unsupported task status: {status}"
        )
    return normalized


def _normalize_statuses(statuses: Sequence[str] | None) -> tuple[str, ...]:
    if not statuses:
        return ()
    return tuple(_normalize_status(status) for status in statuses)


def _normalize_document_scope_type(scope_type: str) -> str:
    normalized = _normalize_required_text(scope_type, field_name="scope_type").lower()
    if normalized not in {_TASK_DOCUMENT_SCOPE_FLOW, _TASK_DOCUMENT_SCOPE_TASK}:
        raise TaskFlowServiceError(
            error_code="invalid_task_document_scope",
            reason=f"Unsupported Task Flow document scope: {scope_type}",
        )
    return normalized


def _normalize_document_key(document_key: str) -> str:
    normalized = _normalize_required_text(document_key, field_name="document_key").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", normalized):
        raise TaskFlowServiceError(
            error_code="invalid_task_document_key",
            reason="document_key must use lowercase letters, numbers, dot, underscore, or dash",
        )
    return normalized


def _ensure_canonical_document_key(*, scope_type: str, document_key: str) -> None:
    if scope_type == _TASK_DOCUMENT_SCOPE_FLOW:
        allowed_keys = CANONICAL_FLOW_DOCUMENT_KEYS
    elif scope_type == _TASK_DOCUMENT_SCOPE_TASK:
        allowed_keys = TASK_WORKING_DOCUMENT_KEYS
    else:
        return
    if document_key in allowed_keys:
        return
    allowed = ", ".join(allowed_keys)
    raise TaskFlowServiceError(
        error_code="invalid_task_document_key",
        reason=f"{scope_type} document_key must be one of: {allowed}",
    )


def _ensure_document_uses_current_contract(document: TaskDocument) -> None:
    """Reject legacy TaskDocument rows on id-based current runtime/API surfaces."""

    _ensure_canonical_document_key(
        scope_type=document.scope_type,
        document_key=document.document_key,
    )


def _normalize_document_body(body: str | None) -> str:
    normalized = str(body or "").strip()
    if not normalized:
        raise TaskFlowServiceError(
            error_code="invalid_task_document_body",
            reason="document body is required",
        )
    return normalized


def _normalize_create_task_status(status: str | None) -> str | None:
    """Normalize one optional create-time task status."""

    normalized = _normalize_status(status) if status is not None else None
    if normalized in {None, "plan", "todo"}:
        return normalized
    raise TaskFlowServiceError(
        error_code="invalid_status",
        reason="New tasks may start only in PLAN or Todo",
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
                f"{_MAX_TASK_ATTACHMENT_BASE64_BYTES} base64 characters"
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
    normalized_type = normalize_task_owner_type(owner_type)
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
            reason="Task Flow owner/reviewer principals must be employees",
        )
    if normalized_type == EMPLOYEE_OWNER_TYPE and parse_employee_owner_ref(normalized_ref) is None:
        raise TaskFlowServiceError(
            error_code="invalid_owner_ref",
            reason="employee ref must match a profile-local employee id",
        )


def _validate_actor_pair(
    *,
    actor_type: str | None,
    actor_ref: str | None,
    allow_missing: bool = False,
) -> None:
    normalized_type = _normalize_optional_text(actor_type)
    normalized_ref = _normalize_optional_text(actor_ref)
    if normalized_type is None and normalized_ref is None and allow_missing:
        return
    if normalized_type is None or normalized_ref is None:
        raise TaskFlowServiceError(
            error_code="invalid_actor",
            reason="actor_type and actor_ref must be provided together",
        )
    if normalized_type not in _VALID_ACTOR_TYPES:
        raise TaskFlowServiceError(
            error_code="invalid_actor_type",
            reason=f"Unsupported actor type: {normalized_type}",
        )
    if normalized_type == "automation" and _parse_automation_actor_ref(normalized_ref) is None:
        raise TaskFlowServiceError(
            error_code="invalid_actor_ref",
            reason="automation actor_ref must match automation:<profile_id>:<automation_id>",
        )
    if normalized_type == EMPLOYEE_OWNER_TYPE and parse_employee_owner_ref(normalized_ref) is None:
        raise TaskFlowServiceError(
            error_code="invalid_actor_ref",
            reason="employee ref must match a profile-local employee id",
        )


async def _task_actor_has_manager_scope(
    *,
    row: Task,
    settings: Settings | None,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
) -> bool:
    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type == "human" and normalized_actor_ref is not None:
        return True
    if normalized_actor_type != EMPLOYEE_OWNER_TYPE or normalized_actor_ref is None:
        return False
    try:
        chart = await EmployeeService(settings or get_settings()).build_org_chart(
            profile_id=task_profile_id,
        )
    except EmployeeServiceError as exc:
        raise TaskFlowServiceError(error_code=exc.error_code, reason=exc.reason) from exc
    actor_employee = chart.employees.get(normalized_actor_ref)
    if actor_employee is None or actor_employee.status != "active":
        return False
    managed_employee_ids = {
        *actor_employee.can_delegate_to,
        *actor_employee.reports,
        *actor_employee.derived_reports,
    }
    task_employee_refs = {
        ref
        for ref in (row.owner_ref, row.reviewer_ref, row.created_by_ref)
        if ref and ref != normalized_actor_ref
    }
    if row.owner_type == EMPLOYEE_OWNER_TYPE and row.owner_ref in managed_employee_ids:
        return True
    if row.reviewer_type == EMPLOYEE_OWNER_TYPE and row.reviewer_ref in managed_employee_ids:
        return True
    if row.created_by_type == EMPLOYEE_OWNER_TYPE and task_employee_refs.intersection(
        managed_employee_ids
    ):
        return True
    return False


async def _ensure_task_actor_can_manage(
    *,
    row: Task,
    settings: Settings | None,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
) -> None:
    """Allow humans, responsible employees, or managers to mutate durable task state."""

    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type is None and normalized_actor_ref is None:
        return
    if normalized_actor_type == "human" and normalized_actor_ref is not None:
        return
    if await _task_actor_has_manager_scope(
        row=row,
        settings=settings,
        task_profile_id=task_profile_id,
        actor_type=normalized_actor_type,
        actor_ref=normalized_actor_ref,
    ):
        return
    if (
        normalized_actor_type == "automation"
        and normalized_actor_ref is not None
        and row.created_by_type == "automation"
        and row.created_by_ref == normalized_actor_ref
    ):
        return
    if (
        normalized_actor_type == EMPLOYEE_OWNER_TYPE
        and normalized_actor_ref is not None
        and row.created_by_type == EMPLOYEE_OWNER_TYPE
        and row.created_by_ref == normalized_actor_ref
    ):
        return
    if (
        normalized_actor_type == EMPLOYEE_OWNER_TYPE
        and normalized_actor_ref is not None
        and row.owner_type == EMPLOYEE_OWNER_TYPE
        and row.owner_ref == normalized_actor_ref
    ):
        return
    raise TaskFlowServiceError(
        error_code="task_actor_forbidden",
        reason="Task cannot be changed by this actor",
    )


async def _ensure_employee_completion_actor_allowed(
    *,
    row: Task,
    settings: Settings | None,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
) -> None:
    """Prevent manager employees from certifying subordinate execution as complete."""

    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type != EMPLOYEE_OWNER_TYPE or normalized_actor_ref is None:
        return
    if row.owner_type == EMPLOYEE_OWNER_TYPE and row.owner_ref == normalized_actor_ref:
        return
    if await _task_actor_has_manager_scope(
        row=row,
        settings=settings,
        task_profile_id=task_profile_id,
        actor_type=normalized_actor_type,
        actor_ref=normalized_actor_ref,
    ):
        raise TaskFlowServiceError(
            error_code="task_manager_completion_forbidden",
            reason=(
                "Manager employees cannot complete subordinate execution tasks. "
                "The assigned employee, a human operator, or the runtime must complete "
                "the delegated work."
            ),
        )


async def _ensure_flow_actor_can_manage(
    *,
    settings: Settings | None,
    profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
) -> None:
    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type == "human" and normalized_actor_ref is not None:
        return
    if normalized_actor_type == "automation" and normalized_actor_ref is not None:
        _ensure_ai_actor_admitted_to_backlog(
            settings=settings,
            task_profile_id=profile_id,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            error_code="task_actor_forbidden",
            reason="Automation actor is not allowed to manage this flow",
        )
        return
    if normalized_actor_type != EMPLOYEE_OWNER_TYPE or normalized_actor_ref is None:
        raise TaskFlowServiceError(
            error_code="task_actor_forbidden",
            reason="Task flow cannot be changed by this actor",
        )
    try:
        chart = await EmployeeService(settings or get_settings()).build_org_chart(
            profile_id=profile_id,
        )
    except EmployeeServiceError as exc:
        raise TaskFlowServiceError(error_code=exc.error_code, reason=exc.reason) from exc
    employee = chart.employees.get(normalized_actor_ref)
    if employee is None or employee.status != "active":
        raise TaskFlowServiceError(
            error_code="task_actor_forbidden",
            reason="Employee actor is not allowed to manage this flow",
        )
    if employee.reports or employee.derived_reports or employee.can_delegate_to:
        return
    raise TaskFlowServiceError(
        error_code="task_actor_forbidden",
        reason="Task flow can only be changed by a human operator or manager employee",
    )


async def _ensure_ai_owner_assignment_allowed(
    *,
    settings: Settings | None,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
    owner_type: str | None,
    owner_ref: str | None,
) -> None:
    """Restrict employee ownership changes to humans, automations, self, or delegates."""

    normalized_owner_type = normalize_task_owner_type(owner_type)
    normalized_owner_ref = _normalize_optional_text(owner_ref)
    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_owner_type != EMPLOYEE_OWNER_TYPE or normalized_owner_ref is None:
        return
    if normalized_actor_type == "human" and normalized_actor_ref is not None:
        return
    if normalized_actor_type == "automation" and normalized_actor_ref is not None:
        _ensure_ai_actor_admitted_to_backlog(
            settings=settings,
            task_profile_id=task_profile_id,
            actor_type=normalized_actor_type,
            actor_ref=normalized_actor_ref,
            error_code="task_owner_forbidden",
            reason="Automation actor is not allowed to assign tasks in this project",
        )
        return
    if normalized_actor_type != EMPLOYEE_OWNER_TYPE or normalized_actor_ref is None:
        return
    if normalized_actor_ref == normalized_owner_ref:
        return
    try:
        chart = await EmployeeService(settings or get_settings()).build_org_chart(
            profile_id=task_profile_id,
        )
    except EmployeeServiceError as exc:
        raise TaskFlowServiceError(error_code=exc.error_code, reason=exc.reason) from exc
    actor_employee = chart.employees.get(normalized_actor_ref)
    if actor_employee is None:
        raise TaskFlowServiceError(
            error_code="task_owner_forbidden",
            reason="Employee actor is not allowed to assign tasks in this project",
        )
    if actor_employee.status != "active":
        raise TaskFlowServiceError(
            error_code="task_owner_forbidden",
            reason=f"Employee {actor_employee.id} is {actor_employee.status} and cannot assign tasks",
        )
    allowed_delegate_ids = {
        *actor_employee.can_delegate_to,
        *actor_employee.reports,
        *actor_employee.derived_reports,
    }
    if normalized_owner_ref in allowed_delegate_ids:
        return
    raise TaskFlowServiceError(
        error_code="task_owner_forbidden",
        reason="Employee actor is not allowed to assign tasks to this employee",
    )


async def _ensure_human_intake_targets_root_employee(
    *,
    settings: Settings | None,
    profile_id: str,
    source_type: str | None,
    actor_type: str | None,
    owner_type: str | None,
    owner_ref: str | None,
) -> None:
    """Require public human intake to enter through the active root employee."""

    if settings is None or not bool(settings.taskflow_public_principal_required):
        return
    normalized_actor_type = _normalize_optional_text(actor_type)
    if normalized_actor_type != "human":
        return
    normalized_source_type = (_normalize_optional_text(source_type) or "").lower()
    if normalized_source_type not in _HUMAN_INTAKE_SOURCE_TYPES:
        return
    normalized_owner_type = normalize_task_owner_type(owner_type)
    normalized_owner_ref = _normalize_optional_text(owner_ref)
    if normalized_owner_type != EMPLOYEE_OWNER_TYPE or normalized_owner_ref is None:
        raise TaskFlowServiceError(
            error_code="task_intake_root_required",
            reason="Human Task Flow intake must target the active root employee",
        )
    root_owner_ref = await _resolve_default_employee_owner_ref(
        settings=settings,
        profile_id=profile_id,
    )
    if normalized_owner_ref == root_owner_ref:
        return
    raise TaskFlowServiceError(
        error_code="task_intake_root_required",
        reason="Human Task Flow intake must target the active root employee",
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
    """Allow automation actors only when they belong to the project profile."""

    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type == "automation" and normalized_actor_ref is not None:
        parsed = _parse_automation_actor_ref(normalized_actor_ref)
        if parsed is None or parsed[0] != task_profile_id:
            raise TaskFlowServiceError(error_code=error_code, reason=reason)
        return
    return


def _ensure_ai_actor_session_binding_allowed(
    *,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
    actor_session_id: str | None | object,
    session_id: str | None | object,
    session_profile_id: str | None | object,
) -> None:
    """Restrict AI actors to binding only their current live session."""

    normalized_actor_type = _normalize_optional_text(actor_type)
    normalized_actor_ref = _normalize_optional_text(actor_ref)
    if normalized_actor_type != EMPLOYEE_OWNER_TYPE or normalized_actor_ref is None:
        return
    actor_profile_id = task_profile_id
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
        and _normalize_optional_text(cast(str | None, session_profile_id)) != actor_profile_id
    ):
        raise TaskFlowServiceError(
            error_code="task_session_binding_forbidden",
            reason="AI actor cannot bind its current session to another profile",
        )


def _ensure_public_session_binding_allowed(
    *,
    settings: Settings | None,
    task_profile_id: str,
    actor_type: str | None,
    actor_ref: str | None,
    actor_session_id: str | None | object,
    session_id: str | None | object,
    session_profile_id: str | None | object,
) -> None:
    """Prevent public callers from attaching arbitrary sessions to Task Flow tasks."""

    if settings is None or not bool(settings.taskflow_public_principal_required):
        return
    if session_id is _TASK_FIELD_UNSET and session_profile_id is _TASK_FIELD_UNSET:
        return
    if (
        session_id is not _TASK_FIELD_UNSET
        and _normalize_optional_text(cast(str | None, session_id)) is None
    ):
        return
    normalized_actor_type = _normalize_optional_text(actor_type)
    if normalized_actor_type != EMPLOYEE_OWNER_TYPE or _normalize_optional_text(actor_ref) is None:
        raise TaskFlowServiceError(
            error_code="task_session_binding_forbidden",
            reason="Only a live employee runtime actor can bind task sessions",
        )
    _ensure_ai_actor_session_binding_allowed(
        task_profile_id=task_profile_id,
        actor_type=actor_type,
        actor_ref=actor_ref,
        actor_session_id=actor_session_id,
        session_id=session_id,
        session_profile_id=session_profile_id,
    )


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
        review_actionable=_task_is_review_actionable(row),
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
    dependencies = await repo.list_dependencies_for_tasks(
        task_ids=tuple(row.id for row in row_list)
    )
    attachment_counts = await repo.count_task_attachments_for_tasks(
        task_ids=tuple(row.id for row in row_list)
    )
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
    waiting_for_human = reason_code.startswith("awaiting_human") or reason_code in {
        "awaiting_input",
        "approval_required",
        "review_changes_requested",
    }
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
    """Return whether one database error comes from the active employee uniqueness guard."""

    message = " ".join(
        str(part).strip()
        for part in (
            getattr(exc, "statement", None),
            getattr(exc, "orig", None),
            exc,
        )
        if part is not None
    ).lower()
    return (
        "ux_task_active_employee_owner" in message
        or "ux_task_active_employee_claim_owner" in message
        or "ux_task_active_ai_owner" in message
        or "ux_task_active_ai_claim_owner" in message
    ) or (
        (
            "unique constraint failed" in message
            or "duplicate key value violates unique constraint" in message
        )
        and "owner_ref" in message
        and "profile_id" in message
    )


def _build_task_flow_schema_error(
    exc: IntegrityError | OperationalError | ProgrammingError,
) -> TaskFlowServiceError | None:
    """Return one structured service error for incompatible Task Flow schemas."""

    message = _task_flow_storage_error_message(exc)
    schema_markers = (
        "no such column",
        "has no column named",
        "unknown column",
        "undefined column",
        "no such table",
        "undefined table",
    )
    if not any(marker in message for marker in schema_markers):
        return None
    task_flow_markers = (
        "task",
        "task_event",
        "task_run",
        "flow_id",
        "reviewer_type",
        "reviewer_ref",
        "requires_review",
        "blocked_reason_code",
        "blocked_reason_text",
        "started_at",
        "last_session_profile_id",
        "details_json",
    )
    if not any(marker in message for marker in task_flow_markers):
        return None
    return TaskFlowServiceError(
        error_code="task_flow_schema_incompatible",
        reason=_TASK_FLOW_SCHEMA_INCOMPATIBLE_REASON,
    )


def _task_flow_storage_error_message(
    exc: IntegrityError | OperationalError | ProgrammingError,
) -> str:
    """Flatten one storage exception into a lowercase diagnostic string."""

    return " ".join(
        str(part).strip()
        for part in (
            getattr(exc, "statement", None),
            getattr(exc, "orig", None),
            exc,
        )
        if part is not None
    ).lower()


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


def _to_knowledge_packet_metadata(packet: KnowledgePacket) -> TaskKnowledgePacketMetadata:
    return TaskKnowledgePacketMetadata(
        profile_id=packet.profile_id,
        flow_id=packet.flow_id,
        task_id=packet.task_id,
        context_budget_chars=packet.context_budget_chars,
        documents=tuple(
            TaskKnowledgePacketDocumentMetadata(
                scope_type=document.scope_type,
                scope_id=document.scope_id,
                document_key=document.document_key,
                title=document.title,
                revision=document.revision,
                confirmation_status=document.confirmation_status,
                excerpt=document.excerpt,
            )
            for document in packet.documents
        ),
        missing_flow_document_keys=packet.missing_flow_document_keys,
        unconfirmed_flow_document_keys=packet.unconfirmed_flow_document_keys,
        health_status=packet.health_status,
        ready_for_delegation=packet.ready_for_delegation,
        ready_for_execution=packet.ready_for_execution,
        blocking_reasons=packet.blocking_reasons,
        required_flow_document_keys=packet.required_flow_document_keys,
    )


def _to_task_document_metadata(row: TaskDocument) -> TaskDocumentMetadata:
    return TaskDocumentMetadata(
        id=row.id,
        profile_id=row.profile_id,
        scope_type=row.scope_type,
        scope_id=row.scope_id,
        document_key=row.document_key,
        title=row.title,
        body=row.body,
        revision=row.revision,
        confirmation_status=row.confirmation_status,
        confirmed_revision=row.confirmed_revision,
        confirmed_by_type=row.confirmed_by_type,
        confirmed_by_ref=row.confirmed_by_ref,
        confirmed_at=row.confirmed_at,
        latest_revision_id=row.latest_revision_id,
        created_by_type=row.created_by_type,
        created_by_ref=row.created_by_ref,
        updated_by_type=row.updated_by_type,
        updated_by_ref=row.updated_by_ref,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _to_task_document_revision_metadata(
    row: TaskDocumentRevision,
) -> TaskDocumentRevisionMetadata:
    return TaskDocumentRevisionMetadata(
        id=row.id,
        document_id=row.document_id,
        revision=row.revision,
        title=row.title,
        body=row.body,
        created_by_type=row.created_by_type,
        created_by_ref=row.created_by_ref,
        created_at=row.created_at,
    )


def _to_employee_task_inbox_event_metadata(
    row: TaskEvent,
    *,
    task_title: str,
) -> EmployeeTaskInboxEventMetadata:
    return EmployeeTaskInboxEventMetadata(
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
    if (
        before.blocked_reason_code != after.blocked_reason_code
        or before.blocked_reason_text != after.blocked_reason_text
    ):
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


def _delegated_task_labels(source_task: Task) -> tuple[str, ...]:
    """Inherit source labels except manager-intake routing labels."""

    return tuple(
        label
        for label in _decode_labels(source_task.labels_json)
        if str(label).strip().lower() not in MANAGER_INTAKE_LABELS
    )


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
    return resolve_employee_execution_profile_id(
        owner_type=owner_type,
        owner_ref=owner_ref,
        task_profile_id=profile_id,
    )


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


def _task_matches_review_inbox(*, row: Task, actor_type: str, actor_ref: str) -> bool:
    if not _task_is_review_actionable(row):
        return False
    if row.reviewer_type is not None and row.reviewer_ref is not None:
        return row.reviewer_type == actor_type and row.reviewer_ref == actor_ref
    return row.owner_type == actor_type and row.owner_ref == actor_ref


def _task_is_review_actionable(row: Task) -> bool:
    if row.status == "review":
        return True
    if row.status not in {"claimed", "running"}:
        return False
    return str(getattr(row, "claim_source_status", "") or "").strip() == "review"


def _ensure_review_actor_matches_task(*, row: Task, actor_type: str, actor_ref: str) -> None:
    if _task_matches_review_inbox(row=row, actor_type=actor_type, actor_ref=actor_ref):
        return
    raise TaskFlowServiceError(
        error_code="task_review_actor_mismatch",
        reason="Task review is not assigned to the selected actor",
    )


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
        dependency_row = await repo.get_task(
            profile_id=task.profile_id, task_id=edge.depends_on_task_id
        )
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
        if is_employee_executor_owner_type(promoted.owner_type):
            await _record_task_wake_requested(
                repo=repo,
                task=promoted,
                reason_code="dependencies_satisfied",
                message="Dependencies satisfied; task is ready for AI execution.",
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
                if is_employee_executor_owner_type(promoted.owner_type):
                    await _record_task_wake_requested(
                        repo=repo,
                        task=promoted,
                        reason_code="dependencies_satisfied",
                        message="Dependencies satisfied; task is ready for AI execution.",
                    )
            return task if promoted is None else promoted
        return task
    for edge in dependencies:
        dependency_row = await repo.get_task(
            profile_id=task.profile_id, task_id=edge.depends_on_task_id
        )
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
    settings: Settings | None,
    profile_id: str,
    task_id: str,
    actor_type: str,
    actor_ref: str,
    message: str,
    comment_type: str,
    task_run_id: int | None = None,
) -> TaskEvent:
    details: dict[str, object] = {"comment_type": comment_type}
    mentions = await _extract_active_task_mentions(
        settings=settings,
        profile_id=profile_id,
        message=message,
    )
    if mentions:
        details["mentions"] = mentions
    row = await repo.create_task_event(
        task_id=task_id,
        task_run_id=task_run_id,
        event_type=_TASK_COMMENT_EVENT_TYPE,
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=message,
        details_json=encode_task_event_details(details),
    )
    for mention in mentions:
        await repo.create_task_event(
            task_id=task_id,
            task_run_id=task_run_id,
            event_type="mention_created",
            actor_type=actor_type,
            actor_ref=actor_ref,
            message=message,
            details_json=encode_task_event_details({"mentions": [mention]}),
        )
        await repo.create_task_event(
            task_id=task_id,
            task_run_id=task_run_id,
            event_type="wake_requested",
            actor_type=actor_type,
            actor_ref=actor_ref,
            message="Explicit mention requested AI attention.",
            details_json=encode_task_event_details(
                {
                    "reason_code": "explicit_mention",
                    "owner_type": mention["owner_type"],
                    "owner_ref": mention["owner_ref"],
                    "status": "mentioned",
                    "mentions": [mention],
                }
            ),
        )
    return row


async def _record_task_wake_requested(
    *,
    repo: TaskFlowRepository,
    task: Task,
    reason_code: str,
    actor_type: str | None = None,
    actor_ref: str | None = None,
    message: str | None = None,
    details: dict[str, object] | None = None,
) -> None:
    """Record a visible wake signal for employee-owned runnable or soon-runnable work."""

    owner_type, owner_ref = _task_responsible_employee(task) or (task.owner_type, task.owner_ref)
    await repo.create_task_event(
        task_id=task.id,
        event_type="wake_requested",
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=message or f"Wake requested: {reason_code}",
        details_json=encode_task_event_details(
            {
                "reason_code": reason_code,
                "owner_type": owner_type,
                "owner_ref": owner_ref,
                "reviewer_type": task.reviewer_type,
                "reviewer_ref": task.reviewer_ref,
                "status": task.status,
                "ready_at": task.ready_at.isoformat() if task.ready_at is not None else None,
                **(details or {}),
            }
        ),
    )


def _task_responsible_employee(task: Task) -> tuple[str, str] | None:
    """Return the employee that should receive work/feed signals for a task."""

    if task.status == "review" and task.reviewer_type == EMPLOYEE_OWNER_TYPE and task.reviewer_ref:
        return EMPLOYEE_OWNER_TYPE, task.reviewer_ref
    if task.owner_type == EMPLOYEE_OWNER_TYPE and task.owner_ref:
        return EMPLOYEE_OWNER_TYPE, task.owner_ref
    return None


async def _record_manager_escalation_if_needed(
    *,
    repo: TaskFlowRepository,
    settings: Settings | None,
    task: Task,
    reason_code: str | None,
    reason_text: str | None,
    actor_type: str | None = None,
    actor_ref: str | None = None,
) -> None:
    """Create runnable manager work when a blocked task needs reassignment."""

    if task.owner_type != EMPLOYEE_OWNER_TYPE or not task.owner_ref:
        return
    normalized_reason_code = str(reason_code or "").strip().lower()
    normalized_reason_text = str(reason_text or "").strip()
    if not _requires_manager_escalation_blocker(
        reason_code=normalized_reason_code,
        reason_text=normalized_reason_text,
    ):
        return
    existing_escalations = await repo.list_tasks_by_source(
        profile_id=task.profile_id,
        source_type=_MANAGER_ESCALATION_SOURCE_TYPE,
        source_ref=task.id,
        limit=10,
    )
    open_escalation = next(
        (row for row in existing_escalations if row.status not in _TASK_TERMINAL_STATUSES),
        None,
    )
    if open_escalation is not None:
        await _record_task_wake_requested(
            repo=repo,
            task=open_escalation,
            reason_code=normalized_reason_code or "manager_reassignment_required",
            actor_type=actor_type,
            actor_ref=actor_ref,
            message="Manager escalation is already open and should continue.",
            details={
                "source_task_id": task.id,
                "source_owner_type": task.owner_type,
                "source_owner_ref": task.owner_ref,
                "escalation_type": _MANAGER_ESCALATION_TYPE,
                "blocked_reason_code": normalized_reason_code or None,
                "blocked_reason_text": normalized_reason_text or None,
            },
        )
        return
    manager = await _resolve_manager_escalation_employee(
        settings=settings,
        profile_id=task.profile_id,
        employee_id=task.owner_ref,
    )
    if manager is None:
        await repo.create_task_event(
            task_id=task.id,
            event_type="manager_escalation_unroutable",
            actor_type=actor_type,
            actor_ref=actor_ref,
            message=(
                "Manager escalation could not find the blocked employee's "
                "active direct manager in this profile."
            ),
            details_json=encode_task_event_details(
                {
                    "source_owner_type": task.owner_type,
                    "source_owner_ref": task.owner_ref,
                    "escalation_type": _MANAGER_ESCALATION_TYPE,
                    "blocked_reason_code": normalized_reason_code or None,
                    "blocked_reason_text": normalized_reason_text or None,
                }
            ),
        )
        await _append_task_comment_event(
            repo=repo,
            settings=settings,
            profile_id=task.profile_id,
            task_id=task.id,
            actor_type="system",
            actor_ref="task-flow",
            message=(
                "Manager escalation could not be routed because the blocked "
                "employee has no active direct manager configured."
            ),
            comment_type="system",
        )
        return
    labels = _normalize_labels(
        (
            *_decode_labels(task.labels_json),
            "manager-escalation",
            "autonomous-routing",
        )
    )
    now_utc = datetime.now(timezone.utc)
    escalation = await repo.create_task(
        task_id=_new_identifier("task"),
        profile_id=task.profile_id,
        flow_id=task.flow_id,
        title=f"Resolve blocker for {task.title}"[:255],
        description=_build_manager_escalation_description(
            source_task=task,
            manager_id=manager.id,
            reason_code=normalized_reason_code,
            reason_text=normalized_reason_text,
        ),
        status="todo",
        priority=max(int(task.priority or 0), 80),
        due_at=task.due_at,
        ready_at=now_utc,
        owner_type=EMPLOYEE_OWNER_TYPE,
        owner_ref=manager.id,
        reviewer_type=None,
        reviewer_ref=None,
        source_type=_MANAGER_ESCALATION_SOURCE_TYPE,
        source_ref=task.id,
        created_by_type=actor_type or task.owner_type,
        created_by_ref=actor_ref or task.owner_ref,
        labels_json=json.dumps(labels),
        requires_review=False,
        blocked_reason_code=None,
        blocked_reason_text=None,
    )
    await record_task_event(
        repo=repo,
        task_id=escalation.id,
        event_type="created",
        actor_type=actor_type,
        actor_ref=actor_ref,
        to_status=escalation.status,
        details={
            "flow_id": escalation.flow_id,
            "owner_type": escalation.owner_type,
            "owner_ref": escalation.owner_ref,
            "reviewer_type": escalation.reviewer_type,
            "reviewer_ref": escalation.reviewer_ref,
            "priority": escalation.priority,
            "labels": list(labels),
            "depends_on_task_ids": [],
            "requires_review": False,
            "source_type": escalation.source_type,
            "source_ref": escalation.source_ref,
            "escalation_type": _MANAGER_ESCALATION_TYPE,
        },
    )
    await repo.create_task_event(
        task_id=task.id,
        event_type="recovery_action_created",
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=f"Created manager escalation task {escalation.id} for {manager.id}.",
        details_json=encode_task_event_details(
            {
                "owner_type": EMPLOYEE_OWNER_TYPE,
                "owner_ref": manager.id,
                "source_owner_type": task.owner_type,
                "source_owner_ref": task.owner_ref,
                "source_task_id": task.id,
                "recovery_task_id": escalation.id,
                "escalation_type": _MANAGER_ESCALATION_TYPE,
                "blocked_reason_code": normalized_reason_code or None,
                "blocked_reason_text": normalized_reason_text or None,
            }
        ),
    )
    await _append_task_comment_event(
        repo=repo,
        settings=settings,
        profile_id=task.profile_id,
        task_id=task.id,
        actor_type=actor_type or task.owner_type,
        actor_ref=actor_ref or task.owner_ref,
        message=(
            f"Autonomous manager escalation created for {EMPLOYEE_OWNER_TYPE}:{manager.id} "
            f"as task {escalation.id}."
        ),
        comment_type="system",
    )
    await _record_task_wake_requested(
        repo=repo,
        task=escalation,
        reason_code=normalized_reason_code or "manager_reassignment_required",
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=f"Manager escalation requested for {task.owner_type}:{task.owner_ref}.",
        details={
            "source_task_id": task.id,
            "source_owner_type": task.owner_type,
            "source_owner_ref": task.owner_ref,
            "escalation_type": _MANAGER_ESCALATION_TYPE,
            "blocked_reason_code": normalized_reason_code or None,
            "blocked_reason_text": normalized_reason_text or None,
        },
    )


def _knowledge_maintenance_source_ref(flow_id: str) -> str:
    return f"flow:{flow_id}"


def _task_recent_activity_at(row: Task) -> datetime:
    timestamp = row.finished_at or row.updated_at or row.created_at
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


async def _build_knowledge_maintenance_health(
    *,
    repo: TaskFlowRepository,
    flow: TaskFlow,
) -> _KnowledgeMaintenanceHealth:
    """Return bounded reasons that require CTO knowledge maintenance."""

    await _ensure_default_flow_documents(
        repo=repo,
        flow=flow,
        actor_type=flow.created_by_type,
        actor_ref=flow.created_by_ref,
    )
    flow_documents = await repo.list_task_documents(
        profile_id=flow.profile_id,
        scope_type=_TASK_DOCUMENT_SCOPE_FLOW,
        scope_id=flow.id,
    )
    packet = build_knowledge_packet(
        profile_id=flow.profile_id,
        flow_id=flow.id,
        task_id=_knowledge_maintenance_source_ref(flow.id),
        flow_documents=flow_documents,
        task_documents=(),
    )
    blocked_rows = await repo.list_tasks(
        profile_id=flow.profile_id,
        statuses=("blocked",),
        flow_id=flow.id,
        limit=200,
    )
    review_rows = await repo.list_tasks(
        profile_id=flow.profile_id,
        statuses=("review",),
        flow_id=flow.id,
        limit=200,
    )
    blocked_requiring_attention = tuple(
        row
        for row in blocked_rows
        if str(row.blocked_reason_code or "").strip() != "dependency_wait"
    )
    human_review_blockers = tuple(
        row
        for row in blocked_requiring_attention
        if str(row.blocked_reason_code or "").strip() == "human_review_required"
    )
    reasons: list[str] = []
    if packet.missing_flow_document_keys:
        reasons.append("missing_docs:" + ",".join(packet.missing_flow_document_keys))
    if packet.unconfirmed_flow_document_keys:
        reasons.append("unconfirmed_docs:" + ",".join(packet.unconfirmed_flow_document_keys))
    if human_review_blockers:
        reasons.append(f"human_review_required:{len(human_review_blockers)}")
    elif blocked_requiring_attention:
        reasons.append(f"blocked_tasks:{len(blocked_requiring_attention)}")
    return _KnowledgeMaintenanceHealth(
        health_status="needs_attention" if reasons else "ready",
        reasons=tuple(reasons),
        missing_flow_document_keys=packet.missing_flow_document_keys,
        unconfirmed_flow_document_keys=packet.unconfirmed_flow_document_keys,
        open_blocked_task_count=len(blocked_requiring_attention),
        open_review_task_count=len(review_rows),
    )


def _build_knowledge_maintenance_description(
    *,
    flow: TaskFlow,
    health: _KnowledgeMaintenanceHealth,
) -> str:
    reasons = "\n".join(f"- {reason}" for reason in health.reasons) or "- no explicit reason"
    return (
        "Autonomous CTO knowledge maintenance task.\n\n"
        f"Flow: {flow.title} ({flow.id})\n"
        f"Health: {health.health_status}\n\n"
        "Reasons:\n"
        f"{reasons}\n\n"
        "Work mode: knowledge_maintenance.\n"
        "Inspect the Project Knowledge Packet, task board, feed, review queue, and current "
        "flow documents before making changes. Update the canonical flow docs "
        "`brief`, `plan`, `spec`, `decisions`, and `status` so the project can continue "
        "from durable knowledge instead of scattered comments.\n\n"
        "Do not implement specialist work inside this maintenance task. If work is needed, "
        "delegate focused employee-owned tasks with clear dependencies and review "
        "expectations. If a human decision or approval is truly required, keep the task "
        "employee-owned and block it with reason_code `human_review_required`, including "
        "one precise question and the document/task revision that needs review.\n\n"
        "Complete this task only after the knowledge state is ready, delegated, or explicitly "
        "blocked for human review."
    )


async def _resolve_manager_escalation_employee(
    *,
    settings: Settings | None,
    profile_id: str,
    employee_id: str,
) -> EmployeeMetadata | None:
    employee_service = EmployeeService(settings or get_settings())
    try:
        employee = await employee_service.get_employee(
            profile_id=profile_id,
            employee_id=employee_id,
        )
    except EmployeeServiceError:
        return None
    manager_id = str(employee.manager_id or "").strip()
    if not manager_id:
        return None
    try:
        manager = await employee_service.get_employee(
            profile_id=profile_id,
            employee_id=manager_id,
        )
    except EmployeeServiceError:
        return None
    if manager.status != "active":
        return None
    return manager


def _requires_manager_escalation_blocker(
    *,
    reason_code: str | None,
    reason_text: str | None,
) -> bool:
    normalized_reason_code = str(reason_code or "").strip().lower()
    normalized_reason_text = str(reason_text or "").strip().lower()
    return normalized_reason_code in _MANAGER_ESCALATION_BLOCKER_CODES or any(
        marker in normalized_reason_text for marker in _MANAGER_ESCALATION_TEXT_MARKERS
    )


def _canonical_manager_escalation_reason_code(
    *,
    reason_code: str,
    reason_text: str | None,
) -> str:
    normalized_reason_code = str(reason_code or "").strip().lower()
    if normalized_reason_code in _MANAGER_ESCALATION_BLOCKER_CODES:
        return normalized_reason_code
    if _requires_manager_escalation_blocker(
        reason_code=normalized_reason_code,
        reason_text=reason_text,
    ):
        return "manager_reassignment_required"
    return normalized_reason_code


def _build_manager_escalation_description(
    *,
    source_task: Task,
    manager_id: str,
    reason_code: str,
    reason_text: str,
) -> str:
    reason = reason_code or "manager_reassignment_required"
    text = reason_text or "The responsible employee cannot continue without manager routing."
    return "\n".join(
        (
            "Resolve an autonomous Task Flow blocker.",
            "",
            f"Source task: {source_task.id}",
            f"Source owner: {source_task.owner_type}:{source_task.owner_ref}",
            f"Assigned manager: {EMPLOYEE_OWNER_TYPE}:{manager_id}",
            f"Blocker: {reason}",
            f"Details: {text}",
            "",
            "Expected manager actions:",
            "- inspect the source task, comments, dependencies, and flow documents;",
            "- decide whether to reassign, delegate remediation, split work, or escalate upward;",
            "- create any needed remediation/check tasks with explicit dependencies;",
            "- update the source task so it can continue, or leave a concrete blocker comment if it cannot.",
            "",
            "Do not mark this task complete until the source task has a clear next executable owner, dependency, or terminal state.",
        )
    )


def _extract_task_mentions(message: str) -> list[dict[str, str]]:
    """Parse lightweight profile-local @employee mentions from comments."""

    mentions: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _MENTION_PATTERN.finditer(message):
        employee_id = str(match.group(1) or "").strip()
        if not employee_id or parse_employee_owner_ref(employee_id) is None:
            continue
        owner_type = EMPLOYEE_OWNER_TYPE
        owner_ref = employee_id
        token = f"@{employee_id}"
        key = (owner_type, owner_ref)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(
            {
                "owner_type": owner_type,
                "owner_ref": owner_ref,
                "token": token,
            }
        )
    return mentions


async def _extract_active_task_mentions(
    *,
    settings: Settings | None,
    profile_id: str,
    message: str,
) -> list[dict[str, str]]:
    mentions = _extract_task_mentions(message)
    if not mentions:
        return []
    employee_service = EmployeeService(settings or get_settings())
    active_mentions: list[dict[str, str]] = []
    for mention in mentions:
        if mention.get("owner_type") != EMPLOYEE_OWNER_TYPE:
            continue
        employee_id = mention.get("owner_ref") or ""
        try:
            employee = await employee_service.get_employee(
                profile_id=profile_id,
                employee_id=employee_id,
            )
        except EmployeeServiceError:
            continue
        if employee.status == "active":
            active_mentions.append(mention)
    return active_mentions


async def _record_document_event(
    *,
    repo: TaskFlowRepository,
    profile_id: str,
    scope_type: str,
    scope_id: str,
    document: TaskDocument,
    actor_type: str,
    actor_ref: str,
) -> None:
    """Record document updates on task history when the scope is task-like."""

    _ = profile_id
    if scope_type != _TASK_DOCUMENT_SCOPE_TASK:
        return
    await record_task_event(
        repo=repo,
        task_id=scope_id,
        event_type="document_revision_created",
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=f"Updated {document.document_key} document revision {document.revision}.",
        details={
            "document_id": document.id,
            "document_key": document.document_key,
            "revision": document.revision,
            "latest_revision_id": document.latest_revision_id,
        },
    )


async def _record_document_confirmation_event(
    *,
    repo: TaskFlowRepository,
    document: TaskDocument,
    actor_type: str,
    actor_ref: str,
) -> None:
    """Record document confirmations on task history when the scope is task-like."""

    if document.scope_type != _TASK_DOCUMENT_SCOPE_TASK:
        return
    await record_task_event(
        repo=repo,
        task_id=document.scope_id,
        event_type="document_confirmed",
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=f"Confirmed {document.document_key} document revision {document.revision}.",
        details={
            "document_id": document.id,
            "document_key": document.document_key,
            "revision": document.revision,
            "confirmation_status": document.confirmation_status,
        },
    )


async def _record_document_deleted_event(
    *,
    repo: TaskFlowRepository,
    document: TaskDocument,
    actor_type: str,
    actor_ref: str,
) -> None:
    """Record document deletion on task history when the task still exists."""

    if document.scope_type != _TASK_DOCUMENT_SCOPE_TASK:
        return
    task = await repo.get_task(profile_id=document.profile_id, task_id=document.scope_id)
    if task is None:
        return
    await record_task_event(
        repo=repo,
        task_id=document.scope_id,
        event_type="document_deleted",
        actor_type=actor_type,
        actor_ref=actor_ref,
        message=f"Deleted {document.document_key} document revision {document.revision}.",
        details={
            "document_id": document.id,
            "document_key": document.document_key,
            "revision": document.revision,
            "scope_type": document.scope_type,
        },
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
        edge.task_id for edge in dependent_edges if edge.task_id not in skip_ids
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
